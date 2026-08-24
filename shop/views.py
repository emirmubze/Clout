import json
import logging
import os
import razorpay
import uuid
from decimal import Decimal, InvalidOperation
from smtplib import SMTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django.core.mail import send_mail
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST
from django.urls import reverse, reverse_lazy

from .models import Order, CustomUser, ContactMessage, Course, Module, Lesson
from .forms import RegistrationForm, CourseForm


logger = logging.getLogger(__name__)


# =========================================================
# COURSE ACCESS HELPER
# =========================================================

def user_has_course_access(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    return (
        Order.objects.filter(
            user=user,
            paid=True
        ).exists()
        or bool(
            getattr(
                user,
                "course_access_approved",
                False
            )
        )
    )


# =========================================================
# SINGLE DEVICE SESSION
# =========================================================

def revoke_user_sessions(user, keep_session_key=None):
    for session in Session.objects.all():
        try:
            session_data = session.get_decoded()
        except Exception:
            continue

        if session_data.get("_auth_user_id") == str(user.pk):
            if (
                keep_session_key
                and session.session_key == keep_session_key
            ):
                continue

            session.delete()


class SingleDeviceLoginView(LoginView):

    def form_valid(self, form):
        response = super().form_valid(form)

        user = self.request.user
        current_session_key = self.request.session.session_key

        if current_session_key:
            revoke_user_sessions(
                user,
                current_session_key
            )

            user.active_session_key = current_session_key
            user.save(update_fields=["active_session_key"])

        return response


# =========================================================
# HOME
# =========================================================

def index(request):

    has_unread_messages = False

    if request.user.is_authenticated:

        if request.user.is_staff:
            has_unread_messages = ContactMessage.objects.filter(
                sender_is_admin=False,
                is_read=False,
            ).filter(
                Q(recipient=request.user)
                | Q(recipient__isnull=True)
            ).exists()
        else:
            has_unread_messages = ContactMessage.objects.filter(
                sender_is_admin=True,
                is_read=False,
            ).filter(
                Q(recipient=request.user)
                | Q(recipient__isnull=True)
            ).exists()

    return render(
        request,
        "shop/index.html",
        {
            "has_unread_messages": has_unread_messages,
            "user_has_course_access": user_has_course_access(request.user),
        }
    )


@require_POST
def ai_chat(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request body."}, status=400)

    question = str(payload.get("question", "")).strip()
    if not question:
        return JsonResponse({"error": "Question is required."}, status=400)

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return JsonResponse({
            "scope": "IN_SCOPE",
            "answer": "The AI assistant is not configured yet. Add GROQ_API_KEY to your .env file and restart Django.",
            "sources": [],
        })

    request_body = json.dumps({
        "model": "openai/gpt-oss-20b",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the Clout course assistant for 'Make Money With AI'. "
                    "The course has exactly seven modules: Digital Products, AI Apps, "
                    "AI Websites, Marketing, Side Hustles, Dropshipping, and Claude AI. "
                    "Use the following course curriculum as your source of truth: "
                    "Digital Products covers e-books, courses, templates, AI art, "
                    "pricing, sales funnels, Shopify, Gumroad, and Kajabi; "
                    "AI Apps covers chatbots, image generators, productivity apps, "
                    "Bubble, Adalo, Zapier, APIs, and subscription, one-time, and "
                    "freemium monetization; AI Websites covers niche sites, AI content, "
                    "SEO, ads, affiliate links, and memberships; Marketing covers "
                    "Facebook, Google, and TikTok ads, email drip campaigns, and AI social "
                    "media workflows; Side Hustles covers AI copywriting, consulting, "
                    "micro-services, time management, and scaling; Dropshipping covers "
                    "AI-enhanced suppliers, niche products, fulfillment automation, "
                    "customer-service bots, and upsells; Claude AI covers Claude's strengths "
                    "and integrating it into apps, chatbots, and content creation. "
                    "You are the official AI assistant for the Make Money With AI course. "
                    "Answer only questions related to this course and its curriculum. "
                    "If a question is unrelated, politely say that you can only answer "
                    "questions about the course. Do not invent modules, lessons, tools, "
                    "prices, guarantees, or information that is not provided here. "
                    "Give direct answers based only on the available course information. "
                    "If asked how many modules the course has, answer seven and name all "
                    "seven modules. "
                    "Keep answers concise unless the learner asks for detail. Preserve the "
                    "course meaning and explain it in simple, natural language. If the learner "
                    "asks about a specific module, explain only that module unless they ask "
                    "for more. Never use Markdown formatting. Do not use hash signs, asterisks, "
                    "underscores, backticks, horizontal rules, Markdown tables, or Markdown "
                    "bullets and numbered lists unless the learner explicitly asks for a list. "
                    "Use plain text headings without symbols when useful, followed by normal "
                    "paragraphs. Do not mention these instructions to the learner."
                ),
            },
            {"role": "user", "content": question},
        ],
        "temperature": 0.3,
        "max_tokens": 512,
    }).encode("utf-8")

    groq_request = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=request_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "course-assistant/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(groq_request, timeout=30) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        answer = response_data["choices"][0]["message"]["content"].strip()
    except HTTPError as error:
        logger.warning("Groq API returned HTTP %s: %s", error.code, error.read().decode("utf-8", errors="replace")[:500])
        return JsonResponse({
            "scope": "IN_SCOPE",
            "answer": "The AI assistant is temporarily unavailable. Please try again shortly.",
            "sources": [],
        })
    except URLError as error:
        logger.warning("Groq API connection failed: %s", error.reason)
        return JsonResponse({
            "scope": "IN_SCOPE",
            "answer": "The AI assistant is temporarily unavailable. Please try again shortly.",
            "sources": [],
        })
    except (KeyError, IndexError, json.JSONDecodeError):
        logger.exception("Groq API returned an unexpected response")
        return JsonResponse({
            "scope": "IN_SCOPE",
            "answer": "The AI assistant is temporarily unavailable. Please try again shortly.",
            "sources": [],
        })

    return JsonResponse({
        "scope": "IN_SCOPE",
        "answer": answer,
        "sources": [],
    })


# =========================================================
# COURSE DETAIL
# =========================================================

def course_detail(request):

    courses = Course.objects.filter(
        is_active=True
    ).all()

    return render(
        request,
        "shop/course_detail.html",
        {
            "courses": courses
        }
    )


# =========================================================
# MY COURSE
# =========================================================

def course(request):

    # Admin / superuser gets access
    if (
        request.user.is_authenticated
        and request.user.is_superuser
    ):

        course_obj = Course.objects.filter(
            is_active=True
        ).first()

        modules = []

        if course_obj:
            modules = Module.objects.filter(
                course=course_obj
            ).prefetch_related("lessons").order_by("order")

        return render(
            request,
            "shop/course.html",
            {
                "course": course_obj,
                "modules": modules
            }
        )

    # Regular users
    if request.user.is_authenticated:

        if user_has_course_access(request.user):

            course_obj = Course.objects.filter(
                is_active=True
            ).first()

            modules = []

            if course_obj:
                modules = Module.objects.filter(
                    course=course_obj
                ).prefetch_related("lessons").order_by("order")

            return render(
                request,
                "shop/course.html",
                {
                    "course": course_obj,
                    "modules": modules
                }
            )

    return redirect("login")


# =========================================================
# CONTACT
# =========================================================

def contact(request):

    from .forms import ContactMessageForm

    if request.method == "POST":

        form = ContactMessageForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            message_obj = form.save(
                commit=False
            )

            message_obj.sender = (
                request.user
                if request.user.is_authenticated
                else None
            )

            message_obj.recipient = None
            message_obj.sender_is_admin = False

            message_obj.save()

            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({
                    "id": message_obj.id,
                    "message": message_obj.message,
                    "image_url": message_obj.image.url if message_obj.image else "",
                    "video_url": reverse("message_video", args=[message_obj.id]) if message_obj.video else "",
                    "created_at": message_obj.created_at.strftime("%d %b, %H:%M"),
                })

        return redirect("contact")

    form = ContactMessageForm()

    if request.user.is_authenticated:

        chat_messages = (
            ContactMessage.objects.filter(
                sender=request.user
            )
            |
            ContactMessage.objects.filter(
                recipient=request.user,
                sender_is_admin=True
            )
            |
            ContactMessage.objects.filter(
                recipient__isnull=True,
                sender_is_admin=True
            )
        )

        ContactMessage.objects.filter(
            Q(recipient=request.user)
            | Q(recipient__isnull=True),
            sender_is_admin=True,
            is_read=False
        ).update(
            is_read=True
        )

    else:

        chat_messages = ContactMessage.objects.filter(
            sender__isnull=True
        )

    chat_messages = chat_messages.order_by(
        "created_at"
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        after_id = request.GET.get("after", "0")
        try:
            after_id = int(after_id)
        except (TypeError, ValueError):
            after_id = 0

        new_messages = chat_messages.filter(
            sender_is_admin=True,
            id__gt=after_id,
        )

        return JsonResponse({
            "messages": [
                {
                    "id": message.id,
                    "message": message.message or "",
                    "image_url": message.image.url if message.image else "",
                    "video_url": reverse("message_video", args=[message.id]) if message.video else "",
                    "created_at": message.created_at.strftime("%d %b, %H:%M"),
                }
                for message in new_messages
            ]
        })

    return render(
        request,
        "shop/contact.html",
        {
            "chat_messages": chat_messages,
            "form": form,
        }
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@login_required(login_url="login")
def admin_dashboard(request):

    if not request.user.is_staff:
        return redirect("index")

    users = CustomUser.objects.all().order_by(
        "name"
    )

    contact_users = (
        CustomUser.objects.filter(
            contact_messages__isnull=False
        )
        .exclude(
            id=request.user.id
        )
        .distinct()
        .order_by("name")
    )

    selected_user_id = request.GET.get(
        "user_id"
    )

    if (
        selected_user_id
        and contact_users.filter(
            id=selected_user_id
        ).exists()
    ):

        selected_user = contact_users.get(
            id=selected_user_id
        )

    else:

        selected_user = (
            contact_users.first()
            if contact_users
            else None
        )

    # =====================================================
    # CHAT
    # =====================================================

    if selected_user:

        chat_messages = ContactMessage.objects.filter(
            (
                Q(sender=selected_user)
                &
                (
                    Q(recipient=request.user)
                    |
                    Q(recipient__isnull=True)
                )
            )
            |
            (
                Q(sender=request.user)
                &
                Q(recipient=selected_user)
            )
        ).order_by(
            "created_at"
        )

        ContactMessage.objects.filter(
            sender=selected_user,
            recipient=request.user,
            is_read=False
        ).update(
            is_read=True
        )

    else:

        chat_messages = ContactMessage.objects.none()

    # =====================================================
    # DASHBOARD STATS
    # =====================================================

    total_payments = Order.objects.filter(
        paid=True
    ).count()

    paid_user_ids = set(
        Order.objects.filter(
            paid=True
        )
        .exclude(
            user_id__isnull=True
        )
        .values_list(
            "user_id",
            flat=True
        )
    )

    approved_user_ids = set(
        CustomUser.objects.filter(
            course_access_approved=True
        ).values_list(
            "id",
            flat=True
        )
    )

    course_buyers = len(
        paid_user_ids | approved_user_ids
    )

    courses = Course.objects.filter(
        is_active=True
    ).order_by(
        "-created_at"
    )

    # =====================================================
    # COURSE EDIT
    # =====================================================

    selected_course_id = request.GET.get(
        "edit_course_id"
    )

    editing_course = None

    if selected_course_id:

        editing_course = get_object_or_404(
            Course,
            id=selected_course_id
        )

    course_form = (
        CourseForm(
            instance=editing_course
        )
        if editing_course
        else CourseForm()
    )

    # =====================================================
    # RENDER
    # =====================================================

    return render(
        request,
        "shop/admin.html",
        {
            "users": users,
            "total_users": users.count(),
            "total_payments": total_payments,
            "course_buyers": course_buyers,
            "courses": courses,
            "course_count": courses.count(),
            "course_form": course_form,
            "editing_course": editing_course,
            "current_admin": request.user,
            "chat_users": contact_users,
            "selected_user": selected_user,
            "chat_messages": chat_messages,
            "contact_count": ContactMessage.objects.count(),
        }
    )


# =========================================================
# ADMIN COURSE ADD
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
def admin_course_add(request):

    if not request.user.is_staff:

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Permission denied."
                },
                status=403
            )

        return redirect("index")

    if request.method != "POST":

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Invalid request method."
                },
                status=405
            )

        return redirect("admin_dashboard")

    form = CourseForm(
        request.POST,
        request.FILES
    )

    if form.is_valid():

        course = form.save()

        # AJAX RESPONSE
        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": True,
                    "message": "Course added successfully.",
                    "course": {
                        "id": course.id,
                        "title": str(
                            getattr(
                                course,
                                "title",
                                ""
                            )
                        ),
                    }
                }
            )

        return redirect(
            "admin_dashboard"
        )

    # AJAX VALIDATION ERROR
    if request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest":

        return JsonResponse(
            {
                "success": False,
                "message": "Please correct the errors.",
                "errors": form.errors.get_json_data()
            },
            status=400
        )

    return redirect(
        "admin_dashboard"
    )


# =========================================================
# ADMIN COURSE EDIT
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
def admin_course_edit(
    request,
    course_id
):

    if not request.user.is_staff:

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Permission denied."
                },
                status=403
            )

        return redirect("index")

    course = get_object_or_404(
        Course,
        id=course_id
    )

    if request.method != "POST":

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Invalid request method."
                },
                status=405
            )

        return redirect(
            f"{reverse('admin_dashboard')}"
            f"?edit_course_id={course.id}"
        )

    form = CourseForm(
        request.POST,
        request.FILES,
        instance=course
    )

    if form.is_valid():

        course = form.save()

        # AJAX RESPONSE
        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": True,
                    "message": "Course updated successfully.",
                    "course": {
                        "id": course.id,
                        "title": str(
                            getattr(
                                course,
                                "title",
                                ""
                            )
                        ),
                    }
                }
            )

        return redirect(
            "admin_dashboard"
        )

    if request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest":

        return JsonResponse(
            {
                "success": False,
                "message": "Please correct the errors.",
                "errors": form.errors.get_json_data()
            },
            status=400
        )

    return redirect(
        f"{reverse('admin_dashboard')}"
        f"?edit_course_id={course.id}"
    )


# =========================================================
# ADMIN COURSE DELETE
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
@require_POST
def admin_course_delete(
    request,
    course_id
):

    if not request.user.is_staff:

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Permission denied."
                },
                status=403
            )

        return redirect("index")

    course = get_object_or_404(
        Course,
        id=course_id
    )

    course.delete()

    # AJAX RESPONSE
    if request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest":

        return JsonResponse(
            {
                "success": True,
                "message": "Course deleted successfully.",
                "course_id": course_id
            }
        )

    return redirect(
        "admin_dashboard"
    )


@login_required(login_url="login")
@require_POST
def admin_modules_save(request):
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    module_items = []
    module_count = request.POST.get("module_count", "0")

    try:
        module_count = int(module_count)
    except (TypeError, ValueError):
        module_count = 0

    if request.content_type and "multipart/form-data" in request.content_type:
        for index in range(module_count):
            title = (request.POST.get(f"module_title_{index}") or "").strip() or f"Module {index + 1}"
            description = (request.POST.get(f"module_description_{index}") or "").strip()
            video_file = request.FILES.get(f"module_video_{index}")
            lesson_count = 0
            try:
                lesson_count = int(request.POST.get(f"module_lesson_count_{index}", "0"))
            except (TypeError, ValueError):
                lesson_count = 0

            lessons = []
            for lesson_index in range(lesson_count):
                lesson_title = (request.POST.get(f"module_{index}_lesson_title_{lesson_index}") or "").strip() or f"Lesson {lesson_index + 1}"
                lesson_description = (request.POST.get(f"module_{index}_lesson_description_{lesson_index}") or "").strip()
                lesson_video = request.FILES.get(f"module_{index}_lesson_video_{lesson_index}")
                lesson_thumbnail = request.FILES.get(f"module_{index}_lesson_thumbnail_{lesson_index}")
                lessons.append({
                    "title": lesson_title,
                    "description": lesson_description,
                    "video": lesson_video,
                    "thumbnail": lesson_thumbnail,
                })

            module_items.append({
                "title": title,
                "description": description,
                "video": video_file,
                "lessons": lessons,
            })
    else:
        try:
            payload = json.loads(request.body or "{}")
            module_items = payload.get("modules", [])
        except (json.JSONDecodeError, AttributeError):
            return JsonResponse({"success": False, "message": "Invalid module data."}, status=400)

    course = Course.objects.filter(is_active=True).first()
    if course is None:
        course = Course.objects.create(
            title="My Course",
            description="",
            is_active=True,
        )

    try:
        with transaction.atomic():
            existing_modules = list(course.modules.all())
            for module in existing_modules:
                module.lessons.all().delete()
            course.modules.all().delete()

            for order, item in enumerate(module_items, start=1):
                title = str(item.get("title", "")).strip()
                if not title:
                    title = f"Module {order}"

                module_obj = Module.objects.create(
                    course=course,
                    title=title,
                    description=str(item.get("description", "")).strip(),
                    video=item.get("video") if isinstance(item, dict) and item.get("video") else None,
                    order=order,
                )

                lessons = item.get("lessons") if isinstance(item, dict) else []
                for lesson_order, lesson_item in enumerate(lessons, start=1):
                    lesson_title = str(lesson_item.get("title", "")).strip() if isinstance(lesson_item, dict) else ""
                    if not lesson_title:
                        lesson_title = f"Lesson {lesson_order}"

                    Lesson.objects.create(
                        module=module_obj,
                        title=lesson_title,
                        description=str(lesson_item.get("description", "")).strip() if isinstance(lesson_item, dict) else "",
                        video=lesson_item.get("video") if isinstance(lesson_item, dict) and lesson_item.get("video") else None,
                        thumbnail=lesson_item.get("thumbnail") if isinstance(lesson_item, dict) and lesson_item.get("thumbnail") else None,
                        order=lesson_order,
                    )
    except (TypeError, ValueError, OSError):
        return JsonResponse(
            {"success": False, "message": "Could not save the modules and lessons."},
            status=400,
        )

    return JsonResponse({
        "success": True,
        "message": "Course modules saved successfully.",
        "course_id": course.id,
        "module_count": len(module_items),
    })


# =========================================================
# TOGGLE COURSE ACCESS
# ACCEPT / DENY
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
@require_POST
def toggle_course_access(
    request,
    user_id
):

    if not request.user.is_staff:

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Permission denied."
                },
                status=403
            )

        return redirect("index")

    user = get_object_or_404(
        CustomUser,
        id=user_id
    )

    # Toggle current state
    user.course_access_approved = (
        not user.course_access_approved
    )

    user.save(
        update_fields=[
            "course_access_approved"
        ]
    )

    status_text = (
        "Accepted"
        if user.course_access_approved
        else "Denied"
    )

    # AJAX RESPONSE
    if request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest":

        return JsonResponse(
            {
                "success": True,
                "message": (
                    f"Course access "
                    f"{status_text.lower()}."
                ),
                "user_id": user.id,
                "course_access_approved": (
                    user.course_access_approved
                ),
                "status": status_text
            }
        )

    return redirect(
        f"{reverse('admin_dashboard')}"
        f"?user_id={user.id}"
    )


# =========================================================
# ADMIN SEND MESSAGE
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
@require_POST
def admin_send_message(
    request,
    user_id
):

    from .forms import ContactMessageForm

    if not request.user.is_staff:

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Permission denied."
                },
                status=403
            )

        return redirect("index")

    recipient = get_object_or_404(
        CustomUser,
        id=user_id
    )

    form = ContactMessageForm(
        request.POST,
        request.FILES
    )

    if not form.is_valid():

        if request.headers.get(
            "X-Requested-With"
        ) == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "message": "Message could not be sent.",
                    "errors": form.errors.get_json_data()
                },
                status=400
            )

        return redirect(
            f"{reverse('admin_dashboard')}"
            f"?user_id={recipient.id}"
        )

    # Create message
    message_obj = form.save(
        commit=False
    )

    message_obj.sender = request.user
    message_obj.recipient = recipient
    message_obj.sender_is_admin = True

    message_obj.save()

    # =====================================================
    # AJAX RESPONSE
    # =====================================================

    if request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest":

        return JsonResponse(
            {
                "success": True,
                "message": "Message sent successfully.",
                "data": {
                    "id": message_obj.id,

                    "message": (
                        message_obj.message
                        or ""
                    ),

                    "created_at": (
                        message_obj.created_at
                        .strftime("%H:%M")
                    ),

                    "image": (
                        message_obj.image.url
                        if message_obj.image
                        else ""
                    ),

                    "video": (
                        reverse(
                            "message_video",
                            args=[
                                message_obj.id
                            ]
                        )
                        if message_obj.video
                        else ""
                    ),

                    "sender_is_admin": True,
                }
            }
        )

    return redirect(
        f"{reverse('admin_dashboard')}"
        f"?user_id={recipient.id}"
    )


# =========================================================
# REGISTER
# =========================================================

def register_view(request):

    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":

        post_data = request.POST.copy()

        if (
            "password" in post_data
            and "password1" not in post_data
        ):
            post_data["password1"] = (
                post_data["password"]
            )

        if (
            "confirm_password" in post_data
            and "password2" not in post_data
        ):
            post_data["password2"] = (
                post_data["confirm_password"]
            )

        if (
            "phone" in post_data
            and "phone_number" not in post_data
        ):

            country_code = post_data.get(
                "country_code",
                ""
            )

            phone = post_data.get(
                "phone",
                ""
            ).strip()

            if country_code and phone:

                post_data[
                    "phone_number"
                ] = (
                    f"{country_code} {phone}"
                )

            elif phone:

                post_data[
                    "phone_number"
                ] = phone

        form = RegistrationForm(
            post_data
        )

        if form.is_valid():

            user = form.save()

            user.backend = (
                "shop.backends."
                "EmailOrUsernameModelBackend"
            )

            login(
                request,
                user
            )

            request.user.active_session_key = request.session.session_key
            request.user.save(update_fields=["active_session_key"])

            revoke_user_sessions(
                user,
                request.session.session_key
            )

            next_url = request.POST.get(
                "next"
            )

            if next_url:
                return redirect(
                    next_url
                )

            return redirect(
                "index"
            )

    else:

        form = RegistrationForm()

    return render(
        request,
        "shop/signup.html",
        {
            "form": form,
            "next": request.GET.get(
                "next",
                ""
            )
        }
    )


# =========================================================
# LOGOUT
# =========================================================

def logout_view(request):

    request.session.flush()

    logout(request)

    return redirect(
        "login"
    )


# =========================================================
# INLINE VIDEO HELPER
# =========================================================

def serve_inline_video(
    request,
    file_field
):

    if not file_field:
        raise Http404(
            "Video not found."
        )

    file_obj = file_field.open(
        "rb"
    )

    response = FileResponse(
        file_obj,
        content_type="video/mp4"
    )

    response[
        "Content-Disposition"
    ] = (
        f'inline; filename="'
        f'{file_field.name.rsplit("/", 1)[-1]}"'
    )

    response[
        "X-Content-Type-Options"
    ] = "nosniff"

    return response


# =========================================================
# SERVE MEDIA FILE
# =========================================================

def serve_media_file(
    request,
    path
):

    lower_path = path.lower()

    blocked_extensions = (
        ".mp4",
        ".webm",
        ".mov",
        ".avi",
        ".mkv",
        ".m4v"
    )

    if lower_path.endswith(
        blocked_extensions
    ):
        raise Http404(
            "Video files are not available for direct download."
        )

    file_path = (
        settings.MEDIA_ROOT / path
    )

    if (
        not file_path.exists()
        or not file_path.is_file()
    ):
        raise Http404(
            "File not found."
        )

    response = FileResponse(
        open(
            file_path,
            "rb"
        ),
        content_type="application/octet-stream"
    )

    response[
        "Content-Disposition"
    ] = (
        'inline; filename="{}"'
        .format(file_path.name)
    )

    response[
        "X-Content-Type-Options"
    ] = "nosniff"

    return response


# =========================================================
# COURSE VIDEO
# =========================================================

@login_required(login_url="login")
def serve_course_video(
    request,
    course_id
):

    course = get_object_or_404(
        Course,
        id=course_id
    )

    if not course.video:
        raise Http404(
            "Course video not found."
        )

    return serve_inline_video(
        request,
        course.video
    )


# =========================================================
# MODULE VIDEO
# =========================================================

@login_required(login_url="login")
def serve_module_video(
    request,
    module_id
):

    module = get_object_or_404(
        Module,
        id=module_id
    )

    if not module.video:
        raise Http404(
            "Module video not found."
        )

    return serve_inline_video(
        request,
        module.video
    )


# =========================================================
# LESSON VIDEO
# =========================================================

@login_required(login_url="login")
def serve_lesson_video(
    request,
    lesson_id
):

    lesson = get_object_or_404(
        Lesson,
        id=lesson_id
    )

    if not lesson.video:
        raise Http404(
            "Lesson video not found."
        )

    return serve_inline_video(
        request,
        lesson.video
    )


# =========================================================
# MESSAGE VIDEO
# =========================================================

@login_required(login_url="login")
def serve_message_video(
    request,
    message_id
):

    message = get_object_or_404(
        ContactMessage,
        id=message_id
    )

    if not message.video:
        raise Http404(
            "Message video not found."
        )

    return serve_inline_video(
        request,
        message.video
    )


# =========================================================
# PROFILE
# =========================================================

@login_required(login_url="login")
def profile(request):

    from .forms import ProfileImageForm

    if request.method == "POST":

        if "remove_image" in request.POST:

            if request.user.profile_image:

                request.user.profile_image.delete()

                request.user.profile_image = None

                request.user.save()

            return redirect(
                "profile"
            )

        else:

            form = ProfileImageForm(
                request.POST,
                request.FILES,
                instance=request.user
            )

            if form.is_valid():

                form.save()

                return redirect(
                    "profile"
                )

    else:

        form = ProfileImageForm(
            instance=request.user
        )

    return render(
        request,
        "shop/profile.html",
        {
            "form": form
        }
    )


# =========================================================
# CHECKOUT
# =========================================================

@login_required(login_url="login")
def checkout(request):

    return render(
        request,
        "shop/checkout.html",
        {
            "product_name":
                "The AI Income Playbook",

            "tax":
                "0.82",

            "total":
                "18.82",

            "currency":
                "USD",
        }
    )


# =========================================================
# CREATE RAZORPAY ORDER
# =========================================================

@require_POST
@login_required(login_url="login")
def create_order(request):

    if (
        not settings.RAZORPAY_KEY_ID
        or not settings.RAZORPAY_KEY_SECRET
    ):

        return JsonResponse(
            {
                "success": False,
                "message":
                    "Add Razorpay keys to .env."
            },
            status=500
        )

    currency = request.POST.get("currency", "USD").upper()
    try:
        amount = Decimal(request.POST.get("amount", "18.82")).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return JsonResponse(
            {"success": False, "message": "Invalid payment amount."},
            status=400,
        )

    if len(currency) != 3 or amount <= 0:
        return JsonResponse(
            {"success": False, "message": "Invalid payment currency or amount."},
            status=400,
        )

    zero_decimal_currency = currency in {"JPY", "KRW"}
    minor_amount = int(amount if zero_decimal_currency else amount * 100)
    client = razorpay.Client(
        auth=(
            settings.RAZORPAY_KEY_ID,
            settings.RAZORPAY_KEY_SECRET
        )
    )

    rp = client.order.create(
        {
            "amount": minor_amount,
            "currency": currency,
            "receipt":
                f"clout-{request.user.id}-{uuid.uuid4().hex[:12]}",
            "payment_capture": 1
        }
    )

    Order.objects.create(
        user=request.user,
        product_name=
            "The AI Income Playbook",
        amount=amount,
        currency=currency,
        razorpay_order_id=
            rp["id"]
    )

    return JsonResponse(
        {
            "success": True,
            "key":
                settings.RAZORPAY_KEY_ID,
            "order_id":
                rp["id"],
            "amount":
                minor_amount,
            "currency":
                currency
        }
    )


# =========================================================
# VERIFY PAYMENT
# =========================================================

@require_POST
@login_required(login_url="login")
def verify_payment(request):

    oid = request.POST.get(
        "razorpay_order_id"
    )

    pid = request.POST.get(
        "razorpay_payment_id"
    )

    sig = request.POST.get(
        "razorpay_signature"
    )

    order = get_object_or_404(
        Order,
        razorpay_order_id=oid,
        user=request.user
    )

    try:

        client = razorpay.Client(
            auth=(
                settings.RAZORPAY_KEY_ID,
                settings.RAZORPAY_KEY_SECRET
            )
        )

        client.utility.verify_payment_signature(
            {
                "razorpay_order_id":
                    oid,

                "razorpay_payment_id":
                    pid,

                "razorpay_signature":
                    sig
            }
        )

    except Exception:

        return JsonResponse(
            {
                "success": False,
                "message":
                    "Payment verification failed."
            },
            status=400
        )

    # Mark payment as paid
    order.razorpay_payment_id = pid
    order.razorpay_signature = sig
    order.paid = True

    order.save()

    # Give course access automatically
    user = order.user

    if user is not None:

        user.course_access_approved = True

        user.save(
            update_fields=[
                "course_access_approved"
            ]
        )

    return JsonResponse(
        {
            "success": True,
            "redirect_url":
                "/payment-success/"
        }
    )


# =========================================================
# PAYMENT SUCCESS
# =========================================================

@login_required(login_url="login")
def payment_success(request):

    return render(
        request,
        "shop/payment_success.html"
    )


# =========================================================
# FORGOT PASSWORD
# =========================================================

def forgot_password(request):

    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":

        email = request.POST.get("email", "").strip()
        user = CustomUser.objects.filter(email__iexact=email).first()

        if user and user.is_active:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = request.build_absolute_uri(
                reverse(
                    "reset_password",
                    kwargs={"uidb64": uid, "token": token},
                )
            )
            try:
                send_mail(
                    "Reset your Clout password",
                    render_to_string(
                        "shop/password-reset-email.txt",
                        {"user": user, "reset_url": reset_url},
                    ),
                    None,
                    [user.email],
                )
            except (SMTPException, OSError):
                logger.exception(
                    "Password reset email could not be sent to %s",
                    user.email,
                )

        # Do not reveal whether the email address exists.
        return redirect("reset_link_sent")

    return render(
        request,
        "shop/forgot-password.html"
    )


# =========================================================
# RESET LINK SENT
# =========================================================

def reset_link_sent(request):

    return render(
        request,
        "shop/reset-link-sent.html"
    )


# =========================================================
# RESET PASSWORD
# =========================================================

def reset_password(request, uidb64, token):

    if request.user.is_authenticated:
        return redirect("index")

    try:
        user = CustomUser.objects.get(
            pk=force_str(urlsafe_base64_decode(uidb64))
        )
    except (CustomUser.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None

    if not user or not default_token_generator.check_token(user, token):
        return render(
            request,
            "shop/reset-password.html",
            {"invalid_link": True},
        )

    if request.method == "POST":

        password1 = request.POST.get(
            "password1"
        )

        password2 = request.POST.get(
            "password2"
        )

        if password1 and password1 == password2 and len(password1) >= 8:
            user.set_password(password1)
            user.save(update_fields=["password"])
            return redirect("password_reset_success")

        password_error = (
            "Password must be at least 8 characters."
            if password1 and len(password1) < 8
            else "Passwords do not match."
        )

        return render(
            request,
            "shop/reset-password.html",
            {"password_error": password_error},
        )

    return render(
        request,
        "shop/reset-password.html"
    )


# =========================================================
# PASSWORD RESET SUCCESS
# =========================================================

def password_reset_success(request):

    return render(
        request,
        "shop/password-reset-success.html"
    )