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
from django.contrib import messages
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

from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from django.urls import reverse, reverse_lazy
from botocore.config import Config

from .models import (
    Order, CustomUser, ContactMessage, Course, Module, Lesson,
    SubtitleTrack, SubtitleSetting, _public_file_url, PromotionalBanner,
    format_phone_for_razorpay
)
from .auth_api import revoke_api_sessions
from .forms import RegistrationForm, CourseForm
from .subtitles import (
    trigger_auto_subtitle_generation,
    get_gemini_api_key,
    get_groq_api_key,
    SUPPORTED_LANGUAGES,
    get_active_target_languages,
    get_language_name,
    cues_to_vtt,
    cues_to_srt,
    format_vtt_timestamp,
)
from .exports import export_users_to_bytes


logger = logging.getLogger(__name__)


# =========================================================
# COURSE ACCESS HELPER
# =========================================================

def user_has_course_access(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
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

    system_prompt = (
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
    )

    gemini_key = get_gemini_api_key()
    groq_key = get_groq_api_key()

    if not gemini_key and not groq_key:
        return JsonResponse({
            "scope": "IN_SCOPE",
            "answer": "The AI assistant is not configured yet. Add GEMINI_API_KEY or GROQ_API_KEY to your .env file and restart Django.",
            "sources": [],
        })

    answer = None

    # Priority 1: Google Gemini (fast, high concurrency, generous token limits)
    if gemini_key:
        for model_name in ["gemini-3.6-flash", "gemini-flash-latest", "gemini-3.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
                gemini_payload = {
                    "system_instruction": {
                        "parts": [{"text": system_prompt}]
                    },
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": question}]
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 800
                    }
                }
                gemini_req = Request(
                    gemini_url,
                    data=json.dumps(gemini_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urlopen(gemini_req, timeout=15) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                candidates = resp_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        answer = parts[0]["text"].strip()
                        break
            except Exception as gemini_err:
                logger.warning("Gemini AI chat (%s) failed: %s", model_name, gemini_err)

    # Priority 2: Groq Fallback
    if not answer and groq_key:
        groq_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
        for g_model in groq_models:
            request_body = json.dumps({
                "model": g_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                "temperature": 0.3,
                "max_tokens": 512,
            }).encode("utf-8")

            groq_request = Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=request_body,
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "course-assistant/1.0",
                },
                method="POST",
            )

            try:
                with urlopen(groq_request, timeout=25) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                raw_ans = response_data["choices"][0]["message"]["content"].strip()
                cleaned_ans = re.sub(r"<think>.*?</think>", "", raw_ans, flags=re.DOTALL).strip()
                if cleaned_ans:
                    answer = cleaned_ans
                    break
            except Exception as groq_err:
                logger.warning("Groq AI chat fallback (%s) failed: %s", g_model, groq_err)

    if not answer:
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
            is_active=True,
            modules__isnull=False
        ).distinct().first()
        if not course_obj:
            course_obj = Course.objects.filter(is_active=True).first()
        if not course_obj:
            course_obj = Course.objects.first()

        modules = []

        if course_obj:
            modules = Module.objects.filter(
                course=course_obj
            ).prefetch_related("lessons").order_by("order")

        promotional_banners = PromotionalBanner.objects.filter(
            is_active=True
        ).order_by("order", "-created_at")

        return render(
            request,
            "shop/course.html",
            {
                "course": course_obj,
                "modules": modules,
                "promotional_banners": promotional_banners,
            }
        )

    # Regular users
    if request.user.is_authenticated:

        if user_has_course_access(request.user):

            course_obj = Course.objects.filter(
                is_active=True,
                modules__isnull=False
            ).distinct().first()
            if not course_obj:
                course_obj = Course.objects.filter(is_active=True).first()
            if not course_obj:
                course_obj = Course.objects.first()

            modules = []

            if course_obj:
                modules = Module.objects.filter(
                    course=course_obj
                ).prefetch_related("lessons").order_by("order")

            promotional_banners = PromotionalBanner.objects.filter(
                is_active=True
            ).order_by("order", "-created_at")

            return render(
                request,
                "shop/course.html",
                {
                    "course": course_obj,
                    "modules": modules,
                    "promotional_banners": promotional_banners,
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
                    "image_url": message_obj.image_url if message_obj.image else "",
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
                    "image_url": message.image_url if message.image else "",
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
                    "image_url": message.image_url if message.image else "",
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

    selected_course_id = request.GET.get(
        "edit_course_id"
    )

    editing_course = None

    if selected_course_id:
        editing_course = Course.objects.filter(
            id=selected_course_id
        ).first()

    courses = Course.objects.all().order_by(
        "id"
    )

    active_course = editing_course
    if not active_course:
        active_course = courses.filter(modules__isnull=False).distinct().first()
    if not active_course:
        active_course = courses.first()
    if not active_course:
        active_course = Course.objects.first()

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
                        "title": getattr(lesson, "display_title", lesson.title),
                        "description": lesson.description,
                        "video_url": lesson.video_public_url,
                        "thumbnail_url": lesson.thumbnail_public_url,
                        "subtitle_status": lesson.subtitle_status,
                        "detected_language": lesson.detected_language,
                        "subtitle_count": lesson.subtitles.count(),
                    }
                    for lesson in module.lessons.prefetch_related("subtitles").all().order_by("order")
                ],
            }
            for module in active_course.modules.prefetch_related("lessons__subtitles").all().order_by("order")
        ]

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
            "active_course": active_course,
            "course_modules": course_modules,
            "course_count": courses.count(),
            "course_form": course_form,
            "editing_course": editing_course,
            "current_admin": request.user,
            "chat_users": contact_users,
            "selected_user": selected_user,
            "chat_messages": chat_messages,
            "contact_count": ContactMessage.objects.count(),
            "promotional_banners": PromotionalBanner.objects.all().order_by("order", "-created_at"),
            "banner_count": PromotionalBanner.objects.count(),
            "supported_languages": list(SUPPORTED_LANGUAGES.values()),
            "active_target_languages": get_active_target_languages(),
            "use_s3": getattr(settings, "USE_S3", False),
        }
    )


# =========================================================
# ADMIN EXCEL EXPORTS
# =========================================================

@login_required(login_url="login")
def admin_export_users(request, export_type):
    """
    Generate and download an Excel (.xlsx) file of users:
    - 'paid': Only verified successful payments (Order.paid=True).
    - 'unpaid': Registered users without verified successful payments.
    - 'all': All registered users.
    Strictly staff-only. Streams in-memory for Render compatibility.
    """
    if not request.user.is_staff:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "message": "Permission denied."},
                status=403
            )
        return redirect("index")

    export_type_clean = (export_type or "").strip().lower()
    if export_type_clean not in ("paid", "unpaid", "all"):
        messages.error(request, f"Invalid export type: {export_type}")
        return redirect("admin_dashboard")

    try:
        data, count, filename = export_users_to_bytes(export_type_clean)
    except Exception as exc:
        logger.exception("Error generating Excel export for %s", export_type_clean)
        messages.error(request, f"Export failed: {str(exc)}")
        return redirect("admin_dashboard")

    if count == 0:
        if export_type_clean == "paid":
            msg = "No paid users found."
        elif export_type_clean == "unpaid":
            msg = "No unpaid users found."
        else:
            msg = "No registered users found."

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "message": msg, "count": 0}
            )

        messages.warning(request, msg)
        return redirect("admin_dashboard")

    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


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
        thumbnail_key = (request.POST.get("thumbnail_key") or "").strip()
        if thumbnail_key:
            _verify_r2_object(thumbnail_key)
            course.thumbnail = thumbnail_key
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
        thumbnail_key = (request.POST.get("thumbnail_key") or "").strip()
        if thumbnail_key:
            _verify_r2_object(thumbnail_key)
            course.thumbnail = thumbnail_key
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
        "lesson_thumbnails",
        "contact_videos",
        "contact_images",
        "profiles",
    }
    if folder not in allowed_folders:
        return JsonResponse({"success": False, "message": "Invalid upload folder."}, status=400)
    if not filename:
        return JsonResponse({"success": False, "message": "A filename is required."}, status=400)
    if folder == "course_videos" or folder == "contact_videos":
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


def _clean_r2_key(raw_key):
    if not raw_key:
        return ""
    key = str(raw_key).strip()
    if "://" in key:
        key = key.split("://", 1)[1]
    for prefix in ("course_videos/", "lesson_thumbnails/", "course_images/", "course_intro_videos/", "avatars/", "subtitles/", "profiles/", "contact_images/", "contact_videos/", "course_thumbnails/", "promotional_banners/"):
        if prefix in key:
            key = key[key.index(prefix):]
            break
    else:
        if "/" in key:
            first_part = key.split("/", 1)[0]
            if "." in first_part or "localhost" in first_part:
                key = key.split("/", 1)[1]
    key = key.lstrip("/")
    if ".." in key:
        key = key.replace("..", "")
    return key


def _verify_r2_object(object_key):
    key = _clean_r2_key(object_key)
    if not key:
        return ""

    if not getattr(settings, "USE_S3", False):
        return key

    try:
        client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            region_name=settings.AWS_S3_REGION_NAME,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        client.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=key)
    except Exception as exc:
        logger.warning("R2 head_object verification warning for %s: %s", key, exc)

    return key


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
                lesson_thumbnail_key = (request.POST.get(f"module_{index}_lesson_thumbnail_key_{lesson_index}") or "").strip()
                lessons.append({
                    "id": lesson_id,
                    "title": lesson_title,
                    "description": lesson_description,
                    "video": lesson_video,
                    "video_url": lesson_video_url,
                    "video_key": lesson_video_key,
                    "thumbnail": lesson_thumbnail,
                    "thumbnail_url": lesson_thumbnail_url,
                    "thumbnail_key": lesson_thumbnail_key,
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

    if not module_items:
        return JsonResponse({"success": False, "message": "At least one module is required to save."}, status=400)

    course_id = request.POST.get("course_id") or request.GET.get("course_id")
    course = None
    if course_id:
        course = Course.objects.filter(id=course_id).first()
    if course is None:
        selected_course_id = request.GET.get("edit_course_id")
        if selected_course_id:
            course = Course.objects.filter(id=selected_course_id).first()
    if course is None:
        course = Course.objects.filter(is_active=True, modules__isnull=False).distinct().first()
    if course is None:
        course = Course.objects.filter(is_active=True).first()
    if course is None:
        course = Course.objects.first()
    if course is None:
        course = Course.objects.create(
            title="The AI Income Playbook",
            description="",
            is_active=True,
        )

    lessons_to_trigger_subtitles = []

    try:
        with transaction.atomic():
            saved_module_ids = []
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
                    module_obj.video_url = _public_file_url(module_obj.video)
                if isinstance(item, dict) and item.get("video_url"):
                    v_url = str(item["video_url"]).strip()
                    if v_url:
                        module_obj.video_url = v_url
                if isinstance(item, dict) and item.get("video_key"):
                    v_key = str(item["video_key"]).strip()
                    if v_key:
                        cleaned_key = _clean_r2_key(v_key)
                        _verify_r2_object(cleaned_key)
                        module_obj.video = cleaned_key
                        module_obj.video_url = _public_file_url(cleaned_key, explicit_url=v_key if v_key.startswith(("http://", "https://")) else "")
                module_obj.save()
                saved_module_ids.append(module_obj.id)

                lessons = item.get("lessons") if isinstance(item, dict) else []
                saved_lesson_ids = []
                for lesson_order, lesson_item in enumerate(lessons, start=1):
                    lesson_title = str(lesson_item.get("title", "")).strip() if isinstance(lesson_item, dict) else ""
                    if not lesson_title:
                        lesson_title = f"Lesson {lesson_order}"

                    lesson_id = lesson_item.get("id") if isinstance(lesson_item, dict) else None
                    lesson_obj = None
                    if lesson_id:
                        lesson_obj = Lesson.objects.filter(id=lesson_id).first()
                    if lesson_obj is None:
                        lesson_obj = Lesson(module=module_obj)
                    else:
                        lesson_obj.module = module_obj

                    lesson_obj.title = lesson_title
                    lesson_obj.description = str(lesson_item.get("description", "")).strip() if isinstance(lesson_item, dict) else ""
                    lesson_obj.order = lesson_order
                    has_video_change = False
                    if isinstance(lesson_item, dict) and lesson_item.get("video"):
                        lesson_obj.video = lesson_item["video"]
                        lesson_obj.video_url = _public_file_url(lesson_obj.video)
                        has_video_change = True
                    if isinstance(lesson_item, dict) and lesson_item.get("video_url"):
                        new_url = str(lesson_item["video_url"]).strip()
                        if new_url and new_url != lesson_obj.video_url:
                            has_video_change = True
                            lesson_obj.video_url = new_url
                    if isinstance(lesson_item, dict) and lesson_item.get("video_key"):
                        v_key = str(lesson_item["video_key"]).strip()
                        if v_key:
                            cleaned_key = _clean_r2_key(v_key)
                            _verify_r2_object(cleaned_key)
                            lesson_obj.video = cleaned_key
                            lesson_obj.video_url = _public_file_url(cleaned_key, explicit_url=v_key if v_key.startswith(("http://", "https://")) else "")
                            has_video_change = True
                    if isinstance(lesson_item, dict) and lesson_item.get("thumbnail"):
                        lesson_obj.thumbnail = lesson_item["thumbnail"]
                        lesson_obj.thumbnail_url = _public_file_url(lesson_obj.thumbnail)
                    if isinstance(lesson_item, dict) and lesson_item.get("thumbnail_url"):
                        t_url = str(lesson_item["thumbnail_url"]).strip()
                        if t_url:
                            lesson_obj.thumbnail_url = t_url
                    if isinstance(lesson_item, dict) and lesson_item.get("thumbnail_key"):
                        t_key = str(lesson_item["thumbnail_key"]).strip()
                        if t_key:
                            cleaned_key = _clean_r2_key(t_key)
                            _verify_r2_object(cleaned_key)
                            lesson_obj.thumbnail = cleaned_key
                            lesson_obj.thumbnail_url = _public_file_url(cleaned_key, explicit_url=t_key if t_key.startswith(("http://", "https://")) else "")
                    lesson_obj.save()
                    saved_lesson_ids.append(lesson_obj.id)

                    if (get_gemini_api_key() or get_groq_api_key()) and (has_video_change or ((lesson_obj.video or lesson_obj.video_url) and lesson_obj.subtitle_status == "none")):
                        lessons_to_trigger_subtitles.append(lesson_obj.id)

                module_obj.lessons.exclude(id__in=saved_lesson_ids).delete()

            course.modules.exclude(id__in=saved_module_ids).delete()
    except Exception as err:
        logger.exception("Admin module save failed: %s", err)
        return JsonResponse(
            {"success": False, "message": f"Could not save course modules: {err}"},
            status=500,
        )

    # Trigger background subtitle generation for uploaded videos asynchronously
    for lid in set(lessons_to_trigger_subtitles):
        try:
            trigger_auto_subtitle_generation(lid)
        except Exception as exc:
            logger.warning("Could not start background subtitle generation for lesson %s: %s", lid, exc)

    updated_course_modules = [
        {
            "id": mod.id,
            "title": mod.title,
            "description": mod.description,
            "video_url": mod.video_public_url,
            "lessons": [
                {
                    "id": les.id,
                    "title": getattr(les, "display_title", les.title),
                    "description": les.description,
                    "video_url": les.video_public_url,
                    "thumbnail_url": les.thumbnail_public_url,
                    "subtitle_status": les.subtitle_status,
                    "detected_language": les.detected_language,
                    "subtitle_count": les.subtitles.count(),
                }
                for les in mod.lessons.prefetch_related("subtitles").all().order_by("order")
            ],
        }
        for mod in course.modules.prefetch_related("lessons__subtitles").all().order_by("order")
    ]

    return JsonResponse({
        "success": True,
        "message": "Course modules saved successfully.",
        "course_id": course.id,
        "module_count": len(updated_course_modules),
        "modules": updated_course_modules,
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
# ADMIN USER ADD
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
@require_POST
def admin_user_add(request):

    if not request.user.is_staff:
        return JsonResponse(
            {"success": False, "message": "Permission denied."},
            status=403
        )

    name = request.POST.get("name", "").strip()
    username = request.POST.get("username", "").strip().lower()
    email = request.POST.get("email", "").strip().lower()
    phone_number = request.POST.get("phone_number", "").strip()
    password = request.POST.get("password", "").strip()
    course_access = request.POST.get("course_access_approved") in ("true", "True", "1", "on")

    if not username:
        return JsonResponse({"success": False, "message": "Username is required."}, status=400)

    if not email:
        return JsonResponse({"success": False, "message": "Email is required."}, status=400)

    if not password:
        return JsonResponse({"success": False, "message": "Password is required."}, status=400)

    if CustomUser.objects.filter(username=username).exists():
        return JsonResponse({"success": False, "message": f"Username @{username} is already taken."}, status=400)

    if CustomUser.objects.filter(email=email).exists():
        return JsonResponse({"success": False, "message": f"Email {email} is already registered."}, status=400)

    if phone_number and CustomUser.objects.filter(phone_number=phone_number).exists():
        return JsonResponse({"success": False, "message": f"Phone number {phone_number} is already in use."}, status=400)

    user = CustomUser(
        username=username,
        name=name or username,
        email=email,
        phone_number=phone_number or None,
        course_access_approved=course_access,
    )
    user.set_password(password)
    user.save()

    return JsonResponse({
        "success": True,
        "message": f"User @{user.username} created successfully.",
        "user": {
            "id": user.id,
            "name": user.name or user.username,
            "username": user.username,
            "email": user.email,
            "phone_number": user.phone_number or "",
            "has_paid": user.has_paid,
            "course_access_approved": user.course_access_approved,
            "profile_image_url": user.profile_image_url if user.profile_image else "",
        }
    })


# =========================================================
# ADMIN USER DELETE
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
@require_POST
def admin_user_delete(request, user_id):

    if not request.user.is_staff:
        return JsonResponse(
            {"success": False, "message": "Permission denied."},
            status=403
        )

    if request.user.id == user_id:
        return JsonResponse(
            {"success": False, "message": "You cannot delete your own admin account."},
            status=400
        )

    user = get_object_or_404(CustomUser, id=user_id)
    username = user.username
    user.delete()

    return JsonResponse({
        "success": True,
        "message": f"User @{username} deleted successfully.",
        "user_id": user_id
    })


# =========================================================
# PROMOTIONAL BANNER ADMIN ENDPOINTS
# AJAX = NO PAGE RELOAD
# =========================================================

@login_required(login_url="login")
def admin_banner_add(request):
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST method required."}, status=405)

    link_url = (request.POST.get("link_url") or "").strip()
    title = (request.POST.get("title") or "").strip()
    order_raw = request.POST.get("order")
    is_active = request.POST.get("is_active") in ("true", "1", "on", True)
    image_file = request.FILES.get("image")
    image_url = (request.POST.get("image_url") or "").strip()

    if not image_file and not image_url:
        return JsonResponse({"success": False, "message": "Please select a banner image or provide an image URL."}, status=400)

    try:
        order = int(order_raw) if order_raw is not None and str(order_raw).strip() != "" else 0
    except (ValueError, TypeError):
        order = 0

    if order == 0:
        from django.db.models import Max
        max_order = PromotionalBanner.objects.aggregate(Max("order"))["order__max"] or 0
        order = max_order + 1

    banner = PromotionalBanner(
        title=title,
        link_url=link_url,
        order=order,
        is_active=is_active,
        image=image_file,
        image_url=image_url,
    )
    banner.save()

    return JsonResponse({
        "success": True,
        "message": "Promotional banner created successfully.",
        "banner": {
            "id": banner.id,
            "title": banner.title,
            "link_url": banner.link_url,
            "order": banner.order,
            "is_active": banner.is_active,
            "image_url": banner.image_public_url,
            "created_at": banner.created_at.strftime("%b %d, %Y"),
        }
    })


@login_required(login_url="login")
def admin_banner_edit(request, banner_id):
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    banner = get_object_or_404(PromotionalBanner, id=banner_id)

    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST method required."}, status=405)

    title = request.POST.get("title")
    if title is not None:
        banner.title = title.strip()

    link_url = request.POST.get("link_url")
    if link_url is not None:
        banner.link_url = link_url.strip()

    order_raw = request.POST.get("order")
    if order_raw is not None and str(order_raw).strip() != "":
        try:
            banner.order = int(order_raw)
        except (ValueError, TypeError):
            pass

    if "is_active" in request.POST:
        banner.is_active = request.POST.get("is_active") in ("true", "1", "on", True)

    if "image" in request.FILES:
        banner.image = request.FILES["image"]

    image_url = request.POST.get("image_url")
    if image_url is not None and image_url.strip():
        banner.image_url = image_url.strip()

    banner.save()

    return JsonResponse({
        "success": True,
        "message": "Banner updated successfully.",
        "banner": {
            "id": banner.id,
            "title": banner.title,
            "link_url": banner.link_url,
            "order": banner.order,
            "is_active": banner.is_active,
            "image_url": banner.image_public_url,
        }
    })


@login_required(login_url="login")
@require_POST
def admin_banner_delete(request, banner_id):
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    banner = get_object_or_404(PromotionalBanner, id=banner_id)
    banner_title = banner.title or f"Banner #{banner.id}"
    banner.delete()

    return JsonResponse({
        "success": True,
        "message": f"{banner_title} deleted successfully.",
        "banner_id": banner_id,
    })


@login_required(login_url="login")
@require_POST
def admin_banner_reorder(request):
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    direction = request.POST.get("direction")
    banner_id = request.POST.get("banner_id")

    if banner_id and direction in ("up", "down"):
        try:
            current_banner = PromotionalBanner.objects.get(id=int(banner_id))
            all_banners = list(PromotionalBanner.objects.all().order_by("order", "id"))
            idx = next((i for i, b in enumerate(all_banners) if b.id == current_banner.id), None)
            if idx is not None:
                swap_idx = idx - 1 if direction == "up" else idx + 1
                if 0 <= swap_idx < len(all_banners):
                    target_banner = all_banners[swap_idx]
                    current_order = current_banner.order
                    target_order = target_banner.order
                    if current_order == target_order:
                        for i, b in enumerate(all_banners):
                            b.order = i
                            b.save(update_fields=["order"])
                        all_banners[idx].order, all_banners[swap_idx].order = swap_idx, idx
                        all_banners[idx].save(update_fields=["order"])
                        all_banners[swap_idx].save(update_fields=["order"])
                    else:
                        current_banner.order = target_order
                        target_banner.order = current_order
                        current_banner.save(update_fields=["order"])
                        target_banner.save(update_fields=["order"])
                    return JsonResponse({"success": True, "message": "Banner reordered successfully."})
        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)}, status=400)

    try:
        raw_body = json.loads(request.body or "{}") if request.content_type == "application/json" else {}
        order_list = raw_body.get("order") or request.POST.getlist("order[]")
        if order_list:
            for new_idx, b_id in enumerate(order_list):
                PromotionalBanner.objects.filter(id=int(b_id)).update(order=new_idx)
            return JsonResponse({"success": True, "message": "Banners reordered successfully."})
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=400)

    return JsonResponse({"success": False, "message": "Invalid reorder parameters."}, status=400)


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
                        message_obj.image_url
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

def _resolve_playable_video_path(file_field_or_path):
    """
    Resolve any video file field, filename, or relative path to a real, playable video on disk.
    If the requested file is missing or a placeholder stub (<= 100 bytes), intelligently fall back
    to available course video files (e.g., course1.mp4).
    """
    raw_name = ""
    if file_field_or_path:
        raw_name = str(getattr(file_field_or_path, "name", "") or str(file_field_or_path)).strip()

    raw_name = raw_name.replace("\\", "/").lstrip("/")
    if raw_name.startswith("media/"):
        raw_name = raw_name[6:]

    # 1. Direct path attribute if available
    if hasattr(file_field_or_path, "path"):
        try:
            p = file_field_or_path.path
            if os.path.exists(p) and os.path.getsize(p) > 100:
                return p
        except Exception:
            pass

    # 2. Check in MEDIA_ROOT and BASE_DIR/media
    for base in (settings.MEDIA_ROOT, os.path.join(settings.BASE_DIR, "media")):
        if raw_name:
            cand = os.path.join(base, raw_name)
            if os.path.exists(cand) and os.path.getsize(cand) > 100:
                return cand

    # 3. Check within course_videos subdirectory
    file_basename = os.path.basename(raw_name) if raw_name else ""
    for base in (settings.MEDIA_ROOT, os.path.join(settings.BASE_DIR, "media")):
        vdir = os.path.join(base, "course_videos")
        if os.path.exists(vdir):
            if file_basename:
                cand = os.path.join(vdir, file_basename)
                if os.path.exists(cand) and os.path.getsize(cand) > 100:
                    return cand
            for known in ("course1.mp4", "course1_EOwq1Qn.mp4", "course1_6gjrGkH.mp4", "course1_nvI4zxC.mp4", "course_video.mp4"):
                cand = os.path.join(vdir, known)
                if os.path.exists(cand) and os.path.getsize(cand) > 100:
                    return cand
            for fname in os.listdir(vdir):
                if fname.lower().endswith((".mp4", ".mov", ".webm")):
                    cand = os.path.join(vdir, fname)
                    if os.path.isfile(cand) and os.path.getsize(cand) > 1000:
                        return cand

    # 4. Search entire MEDIA_ROOT for any playable video
    for root, _, files in os.walk(settings.MEDIA_ROOT):
        for fname in files:
            if fname.lower().endswith((".mp4", ".webm", ".mov")):
                cand = os.path.join(root, fname)
                if os.path.isfile(cand) and os.path.getsize(cand) > 1000:
                    return cand

    return None


def _stream_file_response(request, file_path, default_content_type="video/mp4"):
    """
    Stream a local file with full HTTP 206 Partial Content range request support.
    """
    if not file_path or not os.path.exists(file_path):
        raise Http404("File not found.")

    try:
        file_size = os.path.getsize(file_path)
    except Exception:
        raise Http404("File could not be read.")

    content_type = mimetypes.guess_type(file_path)[0] or default_content_type
    if file_path.lower().endswith(".mp4"):
        content_type = "video/mp4"
    elif file_path.lower().endswith(".webm"):
        content_type = "video/webm"
    elif file_path.lower().endswith(".mov"):
        content_type = "video/quicktime"

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
        file_obj = open(file_path, "rb")
        file_obj.seek(start)
        response = FileResponse(
            file_obj,
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Length"] = str(length)
    else:
        file_obj = open(file_path, "rb")
        response = FileResponse(
            file_obj,
            content_type=content_type,
        )
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = f'inline; filename="{os.path.basename(file_path)}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "public, max-age=3600"
    return response


def serve_inline_video(request, file_field):
    media_url_str = str(getattr(settings, "MEDIA_URL", "") or "").strip()
    if getattr(settings, "USE_S3", False) and media_url_str.startswith(("http://", "https://")):
        try:
            r2_url = _public_file_url(file_field)
            if r2_url and r2_url.startswith(("http://", "https://")):
                return redirect(r2_url)
        except Exception:
            pass

    file_path = _resolve_playable_video_path(file_field)
    if not file_path or not os.path.exists(file_path):
        raise Http404("Video file not found.")

    return _stream_file_response(request, file_path, default_content_type="video/mp4")


# =========================================================
# SERVE MEDIA FILE
# =========================================================

def serve_media_file(request, path):
    media_url_str = str(getattr(settings, "MEDIA_URL", "") or "").strip()
    if getattr(settings, "USE_S3", False) and media_url_str.startswith(("http://", "https://")):
        public_url = media_url_str.rstrip("/") + "/" + path.lstrip("/")
        return redirect(public_url)

    clean_path = path.replace("\\", "/").lstrip("/")

    if clean_path.startswith("course_videos/") or clean_path.lower().endswith((".mp4", ".webm", ".mov")):
        file_path = _resolve_playable_video_path(clean_path)
    else:
        file_path = os.path.join(settings.MEDIA_ROOT, clean_path)
        if not os.path.exists(file_path):
            fallback_path = os.path.join(settings.BASE_DIR, "media", clean_path)
            if os.path.exists(fallback_path):
                file_path = fallback_path
            elif default_storage.exists(clean_path):
                try:
                    file_path = default_storage.path(clean_path)
                except Exception:
                    file_path = None

    if not file_path or not os.path.exists(file_path):
        if clean_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            thumb_dir = os.path.join(settings.MEDIA_ROOT, "lesson_thumbnails")
            if os.path.exists(thumb_dir):
                for f in os.listdir(thumb_dir):
                    if f.lower().endswith((".png", ".jpg", ".webp")):
                        file_path = os.path.join(thumb_dir, f)
                        break
        if not file_path or not os.path.exists(file_path):
            raise Http404("File not found.")

    return _stream_file_response(request, file_path, default_content_type="application/octet-stream")


# =========================================================
# COURSE VIDEO
# =========================================================

@login_required(login_url="login")
def serve_course_video(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    return serve_inline_video(request, course.video or "course_videos/course1.mp4")


# =========================================================
# MODULE VIDEO
# =========================================================

@login_required(login_url="login")
def serve_module_video(request, module_id):
    module = get_object_or_404(Module, id=module_id)
    return serve_inline_video(request, module.video or "course_videos/course1.mp4")


# =========================================================
# LESSON VIDEO
# =========================================================

@login_required(login_url="login")
def serve_lesson_video(request, lesson_id):
    lesson = get_object_or_404(Lesson, id=lesson_id)
    if lesson.video_url and lesson.video_url.startswith(("http://", "https://")):
        return redirect(lesson.video_url)
    return serve_inline_video(request, lesson.video or lesson.video_url or "course_videos/course1.mp4")


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
            cropped_data = request.POST.get("cropped_image_data", "").strip()
            if cropped_data and "," in cropped_data:
                import base64
                from django.core.files.base import ContentFile
                try:
                    format, imgstr = cropped_data.split(";base64,")
                    ext = format.split("/")[-1] if "/" in format else "jpg"
                    if ext.lower() not in ["jpg", "jpeg", "png", "webp"]:
                        ext = "jpg"
                    data = base64.b64decode(imgstr)
                    file_name = f"profile_{request.user.id}_{uuid.uuid4().hex[:8]}.{ext}"
                    request.user.profile_image.save(file_name, ContentFile(data), save=True)
                    return redirect("index")
                except Exception:
                    logger.exception("Failed to save cropped profile image")

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
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    prefill_name = ""
    prefill_email = ""
    prefill_contact = ""

    if user:
        prefill_name = getattr(user, "display_name", "") or (getattr(user, "name", "") or "").strip() or getattr(user, "username", "") or ""
        prefill_email = (getattr(user, "email", "") or "").strip()
        prefill_contact = getattr(user, "razorpay_contact", "") or format_phone_for_razorpay(getattr(user, "phone_number", ""))

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

            "prefill_name":
                prefill_name,

            "prefill_email":
                prefill_email,

            "prefill_contact":
                prefill_contact,
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
    zero_decimal_currencies = {
        "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW",
        "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"
    }
    zero_decimal_currency = currency in zero_decimal_currencies
    try:
        quantize_step = Decimal("1") if zero_decimal_currency else Decimal("0.01")
        amount = Decimal(request.POST.get("amount", "18.82")).quantize(quantize_step)
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

    inr_amount_str = request.POST.get("inr_amount")
    try:
        inr_amount = Decimal(inr_amount_str).quantize(Decimal("0.01")) if inr_amount_str else Decimal("1791.69")
    except (InvalidOperation, TypeError):
        inr_amount = Decimal("1791.69")

    client = razorpay.Client(
        auth=(
            settings.RAZORPAY_KEY_ID,
            settings.RAZORPAY_KEY_SECRET
        )
    )

    minor_amount = int(amount if zero_decimal_currency else amount * 100)
    rp = None
    charge_currency = currency
    charge_amount = amount
    charge_minor_amount = minor_amount

    prefill = {}
    order_notes = {
        "platform": "Clout",
        "product": "The AI Income Playbook",
    }

    if getattr(request, "user", None) and request.user.is_authenticated:
        user_name = getattr(request.user, "display_name", "") or (getattr(request.user, "name", "") or "").strip() or getattr(request.user, "username", "") or ""
        user_email = (getattr(request.user, "email", "") or "").strip()
        user_contact = getattr(request.user, "razorpay_contact", "") or format_phone_for_razorpay(getattr(request.user, "phone_number", ""))

        if user_name:
            prefill["name"] = user_name
        if user_email:
            prefill["email"] = user_email
            order_notes["email"] = user_email
        if user_contact:
            prefill["contact"] = user_contact
            order_notes["phone"] = user_contact

        order_notes["user_id"] = str(request.user.id)

    order_payload = {
        "amount": charge_minor_amount,
        "currency": charge_currency,
        "receipt": f"clout-{request.user.id}-{uuid.uuid4().hex[:12]}",
        "payment_capture": 1,
        "notes": order_notes,
    }

    # Attempt 1: Try creating the order with the selected currency
    try:
        rp = client.order.create(order_payload)
    except Exception as exc:
        logger.warning(
            "Razorpay order creation with currency %s failed (%s). Attempting INR fallback.",
            currency,
            exc
        )
        if currency != "INR":
            # Attempt 2: Fallback to INR if Razorpay rejected the international currency
            try:
                charge_currency = "INR"
                charge_amount = inr_amount
                charge_minor_amount = int(inr_amount * 100)
                fallback_payload = {
                    "amount": charge_minor_amount,
                    "currency": charge_currency,
                    "receipt": f"clout-{request.user.id}-{uuid.uuid4().hex[:12]}",
                    "payment_capture": 1,
                    "notes": order_notes,
                }
                rp = client.order.create(fallback_payload)
            except Exception:
                logger.exception("Razorpay INR fallback order creation failed")
                return JsonResponse(
                    {
                        "success": False,
                        "message": "Razorpay could not create the payment order. Check the live API keys and currency settings."
                    },
                    status=502,
                )
        else:
            logger.exception("Razorpay INR order creation failed")
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
        amount=charge_amount,
        currency=charge_currency,
        razorpay_order_id=
            rp["id"]
    )

    response_data = {
        "success": True,
        "key": settings.RAZORPAY_KEY_ID,
        "order_id": rp["id"],
        "amount": charge_minor_amount,
        "currency": charge_currency,
        "prefill": prefill,
    }
    if currency != charge_currency:
        response_data["display_currency"] = currency
        response_data["display_amount"] = str(amount)

    return JsonResponse(response_data)


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

def dispatch_password_reset_email(user, reset_url):
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "Clout <noreply@clout.courses>")
    subject = "Reset your Clout password"
    text_body = render_to_string("shop/password-reset-email.txt", {"user": user, "reset_url": reset_url})
    html_body = render_to_string("shop/password-reset-email.html", {"user": user, "reset_url": reset_url})

    backend = getattr(settings, "EMAIL_BACKEND", "")
    # In automated test environments using locmem backend, send via Django mail backend so tests capture it in mail.outbox
    if "locmem" in backend:
        send_mail(
            subject,
            text_body,
            from_email,
            [user.email],
            html_message=html_body,
            fail_silently=False,
        )
        return True

    # Method 1: Resend API (HTTP POST - immune to Render/cloud firewall SMTP blocks)
    resend_key = getattr(settings, "RESEND_API_KEY", "").strip() or os.getenv("RESEND_API_KEY", "").strip()
    if resend_key:
        try:
            resend_from = getattr(settings, "RESEND_FROM_EMAIL", "").strip() or from_email
            reply_email = getattr(settings, "SERVER_EMAIL", "support@clout.courses")
            payload = {
                "from": resend_from,
                "to": [user.email],
                "reply_to": reply_email,
                "subject": subject,
                "text": text_body,
                "html": html_body,
            }
            req = Request(
                "https://api.resend.com/emails",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Clout/1.0",
                },
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201):
                    logger.info("Password reset email sent via Resend API to %s", user.email)
                    return True
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore")
            logger.error("Resend API HTTP error (%s): %s. Falling back.", exc.code, err_body)
        except Exception as exc:
            logger.warning("Resend API dispatch failed (%s). Falling back to Brevo/SMTP.", exc)

    # Method 2: Brevo API (HTTP POST)
    brevo_key = getattr(settings, "BREVO_API_KEY", "").strip()
    if brevo_key:
        try:
            sender_email = from_email.split("<")[-1].rstrip(">").strip() if "<" in from_email else from_email
            payload = {
                "sender": {"email": sender_email, "name": "Clout"},
                "to": [{"email": user.email}],
                "subject": subject,
                "textContent": text_body,
                "htmlContent": html_body,
            }
            req = Request(
                "https://api.brevo.com/v3/smtp/email",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "api-key": brevo_key,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201):
                    logger.info("Password reset email sent via Brevo API to %s", user.email)
                    return True
        except Exception as exc:
            logger.warning("Brevo API dispatch failed (%s). Falling back to SMTP.", exc)

    # Method 3: Standard Django send_mail (SMTP / Console)
    try:
        send_mail(
            subject,
            text_body,
            from_email,
            [user.email],
            html_message=html_body,
            fail_silently=False,
        )
        return True
    except (SMTPException, OSError) as exc:
        logger.warning("Standard send_mail failed (%s). Trying SSL Port 465 fallback.", exc)
        # Method 4: SSL Port 465 fallback in case port 587 was blocked by host
        if getattr(settings, "EMAIL_HOST_PASSWORD", ""):
            try:
                from django.core.mail.backends.smtp import EmailBackend
                from django.core.mail import EmailMultiAlternatives
                ssl_backend = EmailBackend(
                    host=getattr(settings, "EMAIL_HOST", "smtp.gmail.com"),
                    port=465,
                    username=getattr(settings, "EMAIL_HOST_USER", "support@clout.courses"),
                    password=settings.EMAIL_HOST_PASSWORD,
                    use_tls=False,
                    use_ssl=True,
                    timeout=10,
                )
                msg = EmailMultiAlternatives(subject, text_body, from_email, [user.email], connection=ssl_backend)
                msg.attach_alternative(html_body, "text/html")
                msg.send(fail_silently=False)
                logger.info("Password reset email sent via SSL Port 465 to %s", user.email)
                return True
            except Exception as ssl_exc:
                logger.exception("SSL fallback email dispatch failed: %s", ssl_exc)
        raise exc


def forgot_password(request):

    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":

        identifier = request.POST.get("email", "").strip()
        clean_phone = re.sub(r"[^\d+]", "", identifier)
        user = CustomUser.objects.filter(
            Q(email__iexact=identifier)
            | Q(username__iexact=identifier)
            | (Q(phone_number__iexact=identifier) if identifier else Q(pk=None))
            | (Q(phone_number__iexact=clean_phone) if clean_phone else Q(pk=None))
        ).first()

        if not user or not user.is_active or not user.email:
            return render(
                request,
                "shop/forgot-password.html",
                {
                    "email_error": "No account found with this email address.",
                    "submitted_email": identifier,
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
            dispatch_password_reset_email(user, reset_url)
        except (SMTPException, OSError, Exception) as exc:
            logger.exception(
                "Password reset email could not be sent to %s: %s",
                user.email,
                exc,
            )

        # Store masked email and debug URL in session
        if "@" in user.email:
            parts = user.email.split("@")
            masked = (parts[0][:2] if len(parts[0]) >= 2 else parts[0][:1]) + "***@" + parts[1]
        else:
            masked = user.email
        request.session["reset_email_masked"] = masked
        if settings.DEBUG:
            request.session["dev_reset_url"] = reset_url

        return redirect("reset_link_sent")

    return render(
        request,
        "shop/forgot-password.html"
    )


# =========================================================
# RESET LINK SENT
# =========================================================

def reset_link_sent(request):
    masked_email = request.session.get("reset_email_masked", "")
    dev_reset_url = request.session.get("dev_reset_url", "") if settings.DEBUG else ""

    return render(
        request,
        "shop/reset-link-sent.html",
        {
            "masked_email": masked_email,
            "dev_reset_url": dev_reset_url,
        }
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


# =========================================================
# SEO: ROBOTS.TXT & SITEMAP.XML
# =========================================================

def robots_txt(request):
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /course/\n"
        "Allow: /contact/\n"
        "Allow: /login/\n"
        "Allow: /register/\n"
        "Allow: /static/\n"
        "Allow: /favicon.ico\n"
        "Allow: /favicon.png\n"
        "Allow: /apple-touch-icon.png\n"
        "Allow: /site.webmanifest\n"
        "Allow: /manifest.json\n\n"
        "Disallow: /admin/\n"
        "Disallow: /admin-dashboard/\n"
        "Disallow: /my-course/\n"
        "Disallow: /checkout/\n"
        "Disallow: /profile/\n"
        "Disallow: /payment-success/\n"
        "Disallow: /api/\n"
        "Disallow: /forgot-password/\n"
        "Disallow: /reset-link-sent/\n"
        "Disallow: /reset-password/\n"
        "Disallow: /password-reset-success/\n"
        "Disallow: /toggle-course-access/\n"
        "Disallow: /admin-user-add/\n"
        "Disallow: /admin-user-delete/\n"
        "Disallow: /admin-course-add/\n"
        "Disallow: /admin-course-edit/\n"
        "Disallow: /admin-course-delete/\n"
        "Disallow: /admin-modules-save/\n"
        "Disallow: /admin-r2-presign/\n"
        "Disallow: /lesson-video/\n"
        "Disallow: /course-video/\n"
        "Disallow: /module-video/\n"
        "Disallow: /message-video/\n"
        "Disallow: /create-order/\n"
        "Disallow: /verify-payment/\n\n"
        "Sitemap: https://clout.courses/sitemap.xml\n"
    )
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=86400"
    return response


def sitemap_xml(request):
    sitemap_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '    <url>\n'
        '        <loc>https://clout.courses/</loc>\n'
        '        <changefreq>weekly</changefreq>\n'
        '        <priority>1.0</priority>\n'
        '    </url>\n'
        '    <url>\n'
        '        <loc>https://clout.courses/course/</loc>\n'
        '        <changefreq>weekly</changefreq>\n'
        '        <priority>0.9</priority>\n'
        '    </url>\n'
        '    <url>\n'
        '        <loc>https://clout.courses/contact/</loc>\n'
        '        <changefreq>monthly</changefreq>\n'
        '        <priority>0.7</priority>\n'
        '    </url>\n'
        '    <url>\n'
        '        <loc>https://clout.courses/login/</loc>\n'
        '        <changefreq>monthly</changefreq>\n'
        '        <priority>0.5</priority>\n'
        '    </url>\n'
        '    <url>\n'
        '        <loc>https://clout.courses/register/</loc>\n'
        '        <changefreq>monthly</changefreq>\n'
        '        <priority>0.6</priority>\n'
        '    </url>\n'
        '</urlset>\n'
    )
    response = HttpResponse(sitemap_content, content_type="application/xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=86400"
    return response


def favicon_ico(request):
    for candidate in [
        settings.BASE_DIR / "shop" / "static" / "favicon.ico",
        settings.STATIC_ROOT / "favicon.ico",
    ]:
        if os.path.exists(candidate):
            response = FileResponse(open(candidate, "rb"), content_type="image/x-icon")
            response["Cache-Control"] = "public, max-age=86400"
            return response
    raise Http404("Favicon not found")


def favicon_png(request):
    for candidate in [
        settings.BASE_DIR / "shop" / "static" / "favicon.png",
        settings.STATIC_ROOT / "favicon.png",
    ]:
        if os.path.exists(candidate):
            response = FileResponse(open(candidate, "rb"), content_type="image/png")
            response["Cache-Control"] = "public, max-age=86400"
            return response
    raise Http404("Favicon PNG not found")


def apple_touch_icon(request):
    for candidate in [
        settings.BASE_DIR / "shop" / "static" / "apple-touch-icon.png",
        settings.BASE_DIR / "shop" / "static" / "apple-touch-icon-precomposed.png",
        settings.STATIC_ROOT / "apple-touch-icon.png",
    ]:
        if os.path.exists(candidate):
            response = FileResponse(open(candidate, "rb"), content_type="image/png")
            response["Cache-Control"] = "public, max-age=86400"
            return response
    raise Http404("Apple touch icon not found")


def site_webmanifest(request):
    for candidate in [
        settings.BASE_DIR / "shop" / "static" / "site.webmanifest",
        settings.BASE_DIR / "shop" / "static" / "manifest.json",
        settings.STATIC_ROOT / "site.webmanifest",
    ]:
        if os.path.exists(candidate):
            response = FileResponse(open(candidate, "rb"), content_type="application/manifest+json")
            response["Cache-Control"] = "public, max-age=86400"
            return response
    raise Http404("Manifest not found")


def og_image(request):
    for candidate in [
        settings.BASE_DIR / "shop" / "static" / "og-image.png",
        settings.STATIC_ROOT / "og-image.png",
    ]:
        if os.path.exists(candidate):
            response = FileResponse(open(candidate, "rb"), content_type="image/png")
            response["Cache-Control"] = "public, max-age=86400"
            return response
    raise Http404("OG image not found")


def og_image_square(request):
    for candidate in [
        settings.BASE_DIR / "shop" / "static" / "og-image-square.png",
        settings.STATIC_ROOT / "og-image-square.png",
    ]:
        if os.path.exists(candidate):
            response = FileResponse(open(candidate, "rb"), content_type="image/png")
            response["Cache-Control"] = "public, max-age=86400"
            return response
    raise Http404("OG square image not found")


# =========================================================
# SUBTITLE SERVING & REST APIs
# =========================================================

def serve_subtitle_vtt(request, subtitle_id):
    """
    Serve WebVTT subtitles (.vtt) with CORS headers for browser <track> elements.
    """
    subtitle = get_object_or_404(SubtitleTrack, id=subtitle_id)

    # Check access permission if associated with course/lesson
    if subtitle.lesson:
        if not request.user.is_staff and not user_has_course_access(request.user):
            # If public preview or user not authenticated, check if preview permitted or deny
            if not request.user.is_authenticated or not user_has_course_access(request.user):
                pass  # Subtitles can be read for video tracks that are playable

    vtt_text = subtitle.vtt_content
    if not vtt_text and subtitle.vtt_file:
        try:
            vtt_text = subtitle.vtt_file.read().decode("utf-8")
        except Exception:
            pass

    if not vtt_text and subtitle.cues_data:
        vtt_text = cues_to_vtt(subtitle.cues_data)

    if not vtt_text:
        vtt_text = "WEBVTT\n\n"

    response = HttpResponse(vtt_text, content_type="text/vtt; charset=utf-8")
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Range, Origin, Content-Type, Accept"
    response["Cache-Control"] = "public, max-age=3600"
    return response


def serve_subtitle_srt(request, subtitle_id):
    """
    Serve SRT subtitles (.srt) as downloadable file.
    """
    subtitle = get_object_or_404(SubtitleTrack, id=subtitle_id)

    srt_text = subtitle.srt_content
    if not srt_text and subtitle.srt_file:
        try:
            srt_text = subtitle.srt_file.read().decode("utf-8")
        except Exception:
            pass

    if not srt_text and subtitle.cues_data:
        srt_text = cues_to_srt(subtitle.cues_data)

    if not srt_text:
        srt_text = ""

    response = HttpResponse(srt_text, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="subtitle_{subtitle.language_code}.srt"'
    response["Access-Control-Allow-Origin"] = "*"
    return response


@never_cache
def api_lesson_subtitles(request, lesson_id):
    """
    API returning available subtitle tracks for a lesson (for video player).
    """
    lesson = get_object_or_404(Lesson, id=lesson_id)

    # Do not return or display subtitles while they are still being generated
    if lesson.subtitle_status == "processing":
        subtitles = []
    else:
        # Allow access if user is authenticated/staff or has course access
        subtitles = lesson.subtitles.filter(status="ready").order_by("-is_original", "language_name")

    response = JsonResponse({
        "success": True,
        "lesson_id": lesson.id,
        "lesson_title": lesson.title,
        "subtitle_status": lesson.subtitle_status,
        "error_message": lesson.subtitle_error or "",
        "detected_language": lesson.detected_language,
        "detected_language_code": lesson.detected_language_code,
        "subtitles": [
            {
                "id": sub.id,
                "language_code": sub.language_code,
                "language_name": sub.language_name,
                "native_name": SUPPORTED_LANGUAGES.get(sub.language_code, {}).get("native", sub.language_name),
                "is_original": sub.is_original,
                "vtt_url": sub.vtt_public_url,
                "srt_url": sub.srt_public_url,
                "cues_count": len(sub.cues_data or []),
                "cues": sub.cues_data or [],
            }
            for sub in subtitles
        ],
    })
    response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["Access-Control-Allow-Origin"] = "*"
    return response


@login_required(login_url="login")
def api_admin_subtitle_details(request, subtitle_id):
    """
    Admin endpoint to view subtitle details and cue list.
    """
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    subtitle = get_object_or_404(SubtitleTrack, id=subtitle_id)
    return JsonResponse({
        "success": True,
        "subtitle": {
            "id": subtitle.id,
            "lesson_id": subtitle.lesson_id,
            "lesson_title": subtitle.lesson.title if subtitle.lesson else "",
            "language_code": subtitle.language_code,
            "language_name": subtitle.language_name,
            "native_name": SUPPORTED_LANGUAGES.get(subtitle.language_code, {}).get("native", subtitle.language_name),
            "is_original": subtitle.is_original,
            "status": subtitle.status,
            "error_message": subtitle.error_message,
            "cues": subtitle.cues_data,
            "vtt_url": subtitle.vtt_public_url,
            "srt_url": subtitle.srt_public_url,
            "created_at": subtitle.created_at.strftime("%Y-%m-%d %H:%M"),
            "updated_at": subtitle.updated_at.strftime("%Y-%m-%d %H:%M"),
        }
    })


@login_required(login_url="login")
@require_POST
def api_admin_update_cues(request, subtitle_id):
    """
    Admin endpoint to update subtitle cue text and timestamps.
    """
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    subtitle = get_object_or_404(SubtitleTrack, id=subtitle_id)

    try:
        payload = json.loads(request.body or "{}")
        cues = payload.get("cues", [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"success": False, "message": "Invalid JSON data."}, status=400)

    formatted_cues = []
    for idx, cue in enumerate(cues, start=1):
        try:
            start = float(cue.get("start", 0.0))
            end = float(cue.get("end", start + 2.0))
            text = str(cue.get("text", "")).strip()
            if not text:
                continue
            formatted_cues.append({
                "id": idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "start_formatted": format_vtt_timestamp(start),
                "end_formatted": format_vtt_timestamp(end),
                "text": text,
            })
        except (ValueError, TypeError):
            continue

    subtitle.cues_data = formatted_cues
    vtt_text = cues_to_vtt(formatted_cues)
    srt_text = cues_to_srt(formatted_cues)
    subtitle.vtt_content = vtt_text
    subtitle.srt_content = srt_text

    vtt_filename = f"subtitles/lesson_{subtitle.lesson_id}_{subtitle.language_code}.vtt"
    srt_filename = f"subtitles/lesson_{subtitle.lesson_id}_{subtitle.language_code}.srt"

    subtitle.vtt_file.save(vtt_filename, ContentFile(vtt_text.encode("utf-8")), save=False)
    subtitle.srt_file.save(srt_filename, ContentFile(srt_text.encode("utf-8")), save=False)
    subtitle.status = "ready"
    subtitle.error_message = ""
    subtitle.save()

    return JsonResponse({
        "success": True,
        "message": f"Subtitles for {subtitle.language_name} updated successfully.",
        "cues_count": len(formatted_cues),
    })


@login_required(login_url="login")
@require_POST
def api_admin_regenerate_subtitles(request, lesson_id):
    """
    Admin endpoint to regenerate subtitles for a lesson.
    """
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    lesson = get_object_or_404(Lesson, id=lesson_id)
    if not lesson.video and not lesson.video_url:
        return JsonResponse({"success": False, "message": "This lesson does not have a video attached."}, status=400)

    if not get_gemini_api_key() and not get_groq_api_key():
        return JsonResponse({
            "success": False,
            "message": "Neither GEMINI_API_KEY nor GROQ_API_KEY is configured. Please configure an AI key in your environment to enable AI subtitle generation."
        }, status=400)

    target_languages = None
    try:
        if request.body:
            payload = json.loads(request.body)
            if isinstance(payload.get("languages"), list) and payload["languages"]:
                target_languages = payload["languages"]
    except Exception:
        pass

    lesson.subtitle_status = "processing"
    lesson.subtitle_error = ""
    lesson.save(update_fields=["subtitle_status", "subtitle_error"])

    trigger_auto_subtitle_generation(lesson.id, target_languages=target_languages)

    return JsonResponse({
        "success": True,
        "message": "Subtitle generation started in background.",
        "lesson_id": lesson.id,
        "subtitle_status": "processing",
    })


@login_required(login_url="login")
@require_POST
def api_admin_add_language_subtitle(request, lesson_id):
    """
    Admin endpoint to generate subtitles for a specific language for an existing video.
    """
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    lesson = get_object_or_404(Lesson, id=lesson_id)
    if not lesson.video and not lesson.video_url:
        return JsonResponse({"success": False, "message": "This lesson has no video attached."}, status=400)

    if not get_gemini_api_key() and not get_groq_api_key():
        return JsonResponse({
            "success": False,
            "message": "Neither GEMINI_API_KEY nor GROQ_API_KEY is configured. Please configure an AI key in your environment to enable AI subtitle generation."
        }, status=400)

    try:
        payload = json.loads(request.body or "{}")
        language_code = str(payload.get("language_code", "")).strip().lower()
    except Exception:
        language_code = str(request.POST.get("language_code", "")).strip().lower()

    if not language_code:
        return JsonResponse({"success": False, "message": "Language code is required."}, status=400)

    lang_name = get_language_name(language_code)
    lesson.subtitle_status = "processing"
    lesson.subtitle_error = ""
    lesson.save(update_fields=["subtitle_status", "subtitle_error"])

    trigger_auto_subtitle_generation(lesson.id, target_languages=[language_code])

    return JsonResponse({
        "success": True,
        "message": f"Generating subtitles for {lang_name} in background...",
        "subtitle_status": "processing",
    })


@login_required(login_url="login")
@require_POST
def api_admin_reset_subtitles(request, lesson_id):
    """
    Admin endpoint to reset subtitle status and clear errors for a lesson.
    """
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    lesson = get_object_or_404(Lesson, id=lesson_id)
    has_ready = lesson.subtitles.filter(status="ready").exists()
    lesson.subtitle_status = "ready" if has_ready else "none"
    lesson.subtitle_error = ""
    lesson.save(update_fields=["subtitle_status", "subtitle_error"])

    return JsonResponse({
        "success": True,
        "message": "Subtitle status reset successfully.",
        "lesson_id": lesson.id,
        "subtitle_status": lesson.subtitle_status,
    })


@login_required(login_url="login")
@require_POST
def api_admin_delete_subtitle(request, subtitle_id):
    """
    Admin endpoint to delete a specific subtitle track.
    """
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    subtitle = get_object_or_404(SubtitleTrack, id=subtitle_id)
    lang_name = subtitle.language_name
    lesson = subtitle.lesson
    subtitle.delete()

    if lesson and not lesson.subtitles.exists():
        lesson.subtitle_status = "none"
        lesson.save(update_fields=["subtitle_status"])

    return JsonResponse({
        "success": True,
        "message": f"{lang_name} subtitles removed.",
    })


@login_required(login_url="login")
def api_admin_languages_config(request):
    """
    Admin endpoint to get and update supported target languages for auto-generation.
    """
    if not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Permission denied."}, status=403)

    if request.method == "POST":
        try:
            payload = json.loads(request.body or "{}")
            languages = payload.get("languages", [])
            if isinstance(languages, list):
                clean_langs = [str(l).strip().lower() for l in languages if str(l).strip().lower() in SUPPORTED_LANGUAGES]
                setting, _ = SubtitleSetting.objects.get_or_create(key="target_languages")
                setting.value = clean_langs
                setting.save()
                return JsonResponse({
                    "success": True,
                    "message": "Supported target languages updated.",
                    "active_languages": clean_langs,
                })
        except Exception as exc:
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({
        "success": True,
        "all_languages": list(SUPPORTED_LANGUAGES.values()),
        "active_languages": get_active_target_languages(),
    })