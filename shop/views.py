import json
import logging
import os
import re
import razorpay
import uuid
import boto3
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
from django.core.files.storage import default_storage
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
import mimetypes

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST
from django.urls import reverse, reverse_lazy
from botocore.config import Config

from .models import Order, CustomUser, ContactMessage, Course, Module, Lesson
from .auth_api import revoke_api_sessions
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

    def get_success_url(self):
        if self.request.user.is_staff:
            return reverse("admin_dashboard")

        return super().get_success_url()

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

        revoke_api_sessions(user, keep_session_key=current_session_key)

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

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "user": {
                "id": selected_user.id if selected_user else None,
                "name": selected_user.name if selected_user else "Customer Support",
                "email": selected_user.email if selected_user else "Select a conversation",
            },
            "messages": [
                {
                    "id": message.id,
                    "message": message.message or "",
                    "sender_is_admin": message.sender_is_admin,
                    "image_url": message.image.url if message.image else "",
                    "video_url": reverse("message_video", args=[message.id]) if message.video else "",
                    "created_at": message.created_at.strftime("%H:%M"),
                }
                for message in chat_messages
            ],
        })

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

    active_course = courses.first()
    course_modules = []
    if active_course:
        course_modules = [
            {
                "id": module.id,
                "title": module.title,
                "description": module.description,
                "video_url": module.video_public_url,
                "lessons": [
                    {
                        "id": lesson.id,
                        "title": lesson.title,
                        "description": lesson.description,
                        "video_url": lesson.video_public_url,
                        "thumbnail_url": lesson.thumbnail_public_url,
                    }
                    for lesson in module.lessons.all()
                ],
            }
            for module in active_course.modules.prefetch_related("lessons").all()
        ]

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
            "course_modules": course_modules,
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

        course = form.save(commit=False)
        video_key = (request.POST.get("video_key") or "").strip()
        if video_key:
            _verify_r2_object(video_key)
            course.video = video_key
        course.save()

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

        course = form.save(commit=False)
        video_key = (request.POST.get("video_key") or "").strip()
        if video_key:
            _verify_r2_object(video_key)
            course.video = video_key
        if (
            Order.objects.filter(paid=True).exists()
            or CustomUser.objects.filter(course_access_approved=True).exists()
        ):
            course.is_active = True
        course.save()

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

    if (
        Order.objects.filter(paid=True).exists()
        or CustomUser.objects.filter(course_access_approved=True).exists()
    ):
        message = "This course cannot be deleted while users have course access."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "message": message}, status=409)
        return redirect("admin_dashboard")

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
    try:
        return _admin_modules_save_impl(request)
    except Exception:
        logger.exception("Admin module save request failed")
        return JsonResponse(
            {"success": False, "message": "The server could not process the module upload. Check the R2 credentials, endpoint, bucket, and deployment logs."},
            status=500,
        )


@login_required(login_url="login")
@require_POST
def r2_presign_upload(request):
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)
    if not settings.USE_S3:
        return JsonResponse({"success": False, "message": "Direct R2 uploads are disabled in local storage mode."}, status=400)

    try:
        payload = json.loads(request.body or "{}")
        filename = str(payload.get("filename") or "").strip()
        content_type = str(payload.get("content_type") or "application/octet-stream").strip()
        folder = str(payload.get("folder") or "").strip().strip("/")
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"success": False, "message": "Invalid upload request."}, status=400)

    allowed_folders = {
        "course_videos",
        "course_thumbnails",
        "contact_videos",
        "contact_images",
    }
    if folder not in allowed_folders:
        return JsonResponse({"success": False, "message": "Invalid upload folder."}, status=400)
    if not filename:
        return JsonResponse({"success": False, "message": "A filename is required."}, status=400)
    if folder == "course_videos":
        content_type = "video/mp4"

    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "-", filename).strip(".") or "upload"
    object_key = f"{folder}/{uuid.uuid4().hex}-{safe_filename}"

    try:
        client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            region_name=settings.AWS_S3_REGION_NAME,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        upload_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=900,
            HttpMethod="PUT",
        )
    except Exception:
        logger.exception("Could not create R2 presigned upload URL")
        return JsonResponse(
            {"success": False, "message": "Could not prepare the R2 upload."},
            status=502,
        )

    return JsonResponse({
        "success": True,
        "upload_url": upload_url,
        "object_key": object_key,
    })


def _verify_r2_object(object_key):
    if not object_key or object_key.startswith("/") or ".." in object_key:
        raise ValueError("Invalid R2 object key.")
    if not object_key.startswith(("course_videos/", "course_thumbnails/", "contact_videos/", "contact_images/")):
        raise ValueError("Invalid R2 object folder.")

    client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        region_name=settings.AWS_S3_REGION_NAME,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=object_key)


def _admin_modules_save_impl(request):
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
            module_id = request.POST.get(f"module_id_{index}")
            title = (request.POST.get(f"module_title_{index}") or "").strip() or f"Module {index + 1}"
            description = (request.POST.get(f"module_description_{index}") or "").strip()
            video_file = request.FILES.get(f"module_video_{index}") or None
            video_url = (request.POST.get(f"module_video_url_{index}") or "").strip()
            video_key = (request.POST.get(f"module_video_key_{index}") or "").strip()
            lesson_count = 0
            try:
                lesson_count = int(request.POST.get(f"module_lesson_count_{index}", "0"))
            except (TypeError, ValueError):
                lesson_count = 0

            lessons = []
            for lesson_index in range(lesson_count):
                lesson_id = request.POST.get(f"module_{index}_lesson_id_{lesson_index}")
                lesson_title = (request.POST.get(f"module_{index}_lesson_title_{lesson_index}") or "").strip() or f"Lesson {lesson_index + 1}"
                lesson_description = (request.POST.get(f"module_{index}_lesson_description_{lesson_index}") or "").strip()
                lesson_video = request.FILES.get(f"module_{index}_lesson_video_{lesson_index}") or None
                lesson_video_url = (request.POST.get(f"module_{index}_lesson_video_url_{lesson_index}") or "").strip()
                lesson_video_key = (request.POST.get(f"module_{index}_lesson_video_key_{lesson_index}") or "").strip()
                lesson_thumbnail = request.FILES.get(f"module_{index}_lesson_thumbnail_{lesson_index}") or None
                lesson_thumbnail_url = (request.POST.get(f"module_{index}_lesson_thumbnail_url_{lesson_index}") or "").strip()
                lessons.append({
                    "id": lesson_id,
                    "title": lesson_title,
                    "description": lesson_description,
                    "video": lesson_video,
                    "video_url": lesson_video_url,
                    "video_key": lesson_video_key,
                    "thumbnail": lesson_thumbnail,
                    "thumbnail_url": lesson_thumbnail_url,
                })

            module_items.append({
                "id": module_id,
                "title": title,
                "description": description,
                "video": video_file,
                "video_url": video_url,
                "video_key": video_key,
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
            for order, item in enumerate(module_items, start=1):
                title = str(item.get("title", "")).strip()
                if not title:
                    title = f"Module {order}"

                module_id = item.get("id") if isinstance(item, dict) else None
                module_obj = course.modules.filter(id=module_id).first() if module_id else None
                if module_obj is None:
                    module_obj = Module(course=course)

                module_obj.title = title
                if "description" in item:
                    module_obj.description = str(item.get("description", "")).strip()
                module_obj.order = order
                if isinstance(item, dict) and item.get("video"):
                    module_obj.video = item["video"]
                if isinstance(item, dict) and item.get("video_url"):
                    module_obj.video_url = str(item["video_url"]).strip()
                if isinstance(item, dict) and item.get("video_key"):
                    _verify_r2_object(str(item["video_key"]).strip())
                    module_obj.video = str(item["video_key"]).strip()
                module_obj.save()

                lessons = item.get("lessons") if isinstance(item, dict) else []
                for lesson_order, lesson_item in enumerate(lessons, start=1):
                    lesson_title = str(lesson_item.get("title", "")).strip() if isinstance(lesson_item, dict) else ""
                    if not lesson_title:
                        lesson_title = f"Lesson {lesson_order}"

                    lesson_id = lesson_item.get("id") if isinstance(lesson_item, dict) else None
                    lesson_obj = module_obj.lessons.filter(id=lesson_id).first() if lesson_id else None
                    if lesson_obj is None:
                        lesson_obj = Lesson(module=module_obj)

                    lesson_obj.title = lesson_title
                    lesson_obj.description = str(lesson_item.get("description", "")).strip() if isinstance(lesson_item, dict) else ""
                    lesson_obj.order = lesson_order
                    if isinstance(lesson_item, dict) and lesson_item.get("video"):
                        lesson_obj.video = lesson_item["video"]
                    if isinstance(lesson_item, dict) and lesson_item.get("video_url"):
                        lesson_obj.video_url = str(lesson_item["video_url"]).strip()
                    if isinstance(lesson_item, dict) and lesson_item.get("video_key"):
                        _verify_r2_object(str(lesson_item["video_key"]).strip())
                        lesson_obj.video = str(lesson_item["video_key"]).strip()
                    if isinstance(lesson_item, dict) and lesson_item.get("thumbnail"):
                        lesson_obj.thumbnail = lesson_item["thumbnail"]
                    if isinstance(lesson_item, dict) and lesson_item.get("thumbnail_url"):
                        lesson_obj.thumbnail_url = str(lesson_item["thumbnail_url"]).strip()
                    lesson_obj.save()
    except Exception:
        logger.exception("Admin module save failed")
        return JsonResponse(
            {"success": False, "message": "The R2 upload or database save failed. Check the R2 credentials, endpoint, bucket, and deployment logs."},
            status=502,
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

            revoke_api_sessions(
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

    try:
        request.session.flush()
        logout(request)
    except Exception:
        logger.exception("Logout failed for user %s", request.user)
        return redirect(f"{reverse('login')}?logout_error=1")

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
        raise Http404("Video not found.")

    if settings.USE_S3:
        if not file_field.name:
            raise Http404("Video not found.")

        try:
            client = boto3.client(
                "s3",
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                region_name=settings.AWS_S3_REGION_NAME,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            )
            client.head_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=file_field.name,
            )
            video_url = client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                    "Key": file_field.name,
                    "ResponseContentType": "video/mp4",
                    "ResponseContentDisposition": (
                        f'inline; filename="{file_field.name.rsplit("/", 1)[-1]}"'
                    ),
                },
                ExpiresIn=3600,
                HttpMethod="GET",
            )
            return redirect(video_url)
        except Exception:
            logger.exception(
                "Could not generate R2 video URL for %s",
                file_field.name,
            )
            raise Http404("Video could not be loaded.")

    try:
        file_path = file_field.path
        file_size = file_field.size
    except Exception:
        raise Http404("Video file not found.")

    content_type = mimetypes.guess_type(file_path)[0] or "video/mp4"
    if file_path.lower().endswith(".mp4"):
        content_type = "video/mp4"

    range_header = request.headers.get("Range")
    if range_header and range_header.startswith("bytes="):
        try:
            range_value = range_header.replace("bytes=", "", 1).split(",", 1)[0]
            start_text, _, end_text = range_value.partition("-")
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else file_size - 1
            end = min(end, file_size - 1)
        except (ValueError, TypeError):
            return HttpResponse(
                status=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        if start >= file_size or start > end:
            return HttpResponse(
                status=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        length = end - start + 1
        file_obj = file_field.open("rb")
        file_obj.seek(start)
        response = FileResponse(
            file_obj,
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Length"] = str(length)
    else:
        file_obj = file_field.open("rb")
        response = FileResponse(
            file_obj,
            content_type=content_type,
        )
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = (
        f'inline; filename="{file_field.name.rsplit("/", 1)[-1]}"'
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
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

    if settings.USE_S3:
        public_url = (
            settings.MEDIA_URL.rstrip("/")
            + "/"
            + path.lstrip("/")
        )
        return redirect(public_url)

    if not default_storage.exists(path):
        raise Http404(
            "File not found."
        )

    response = FileResponse(
        default_storage.open(path, "rb"),
        content_type=mimetypes.guess_type(path)[0] or "application/octet-stream"
    )

    response[
        "Content-Disposition"
    ] = (
        'inline; filename="{}"'
        .format(path.rsplit("/", 1)[-1])
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

            return redirect("index")

        else:

            form = ProfileImageForm(
                request.POST,
                request.FILES,
                instance=request.user
            )

            if form.is_valid():

                try:
                    form.save()
                except Exception:
                    logger.exception("Profile image upload failed")
                    form.add_error(
                        "profile_image",
                        "The profile image could not be uploaded. Please try again.",
                    )
                else:
                    return redirect("index")

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
    try:
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
    except Exception:
        logger.exception("Razorpay order creation failed")
        return JsonResponse(
            {
                "success": False,
                "message": "Razorpay could not create the payment order. Check the live API keys and currency settings."
            },
            status=502,
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

        if not user or not user.is_active:
            return render(
                request,
                "shop/forgot-password.html",
                {
                    "email_error": "No account found with this email address.",
                    "submitted_email": email,
                },
            )

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

    if not user or not user.is_active or not default_token_generator.check_token(user, token):
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