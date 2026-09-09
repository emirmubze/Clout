from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.templatetags.static import static
from urllib.parse import quote


def _public_file_url(file_field, explicit_url=""):
    url = str(explicit_url or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    if ".r2.dev/" in url or url.startswith("pub-"):
        return f"https://{url.lstrip('/')}"

    raw_val = ""
    if file_field:
        raw_val = str(getattr(file_field, "name", "") or str(file_field)).strip()
        if raw_val.startswith(("http://", "https://")):
            return raw_val
        if ".r2.dev/" in raw_val or raw_val.startswith("pub-"):
            return f"https://{raw_val.lstrip('/')}"

    target = raw_val or url
    if not target:
        return ""

    if target.startswith(("http://", "https://")):
        return target
    if ".r2.dev/" in target or target.startswith("pub-"):
        return f"https://{target.lstrip('/')}"

    file_name = target.lstrip("/")
    for prefix in ("course_videos/", "lesson_thumbnails/", "course_images/", "course_intro_videos/", "avatars/", "subtitles/"):
        if prefix in file_name:
            file_name = file_name[file_name.index(prefix):]
            break

    custom_domain = (
        str(getattr(settings, "AWS_S3_CUSTOM_DOMAIN", ""))
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .rstrip("/")
    )

    if custom_domain and file_name:
        return f"https://{custom_domain}/{file_name}"

    endpoint = str(getattr(settings, "AWS_S3_ENDPOINT_URL", "")).strip().rstrip("/")
    bucket = str(getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")).strip()
    if getattr(settings, "USE_S3", False) and endpoint and bucket and file_name:
        return f"{endpoint}/{bucket}/{file_name}"

    if file_field:
        try:
            field_url = str(file_field.url or "").strip()
            if field_url:
                return field_url
        except (AttributeError, ValueError):
            pass

    if file_name:
        media_url = str(getattr(settings, "MEDIA_URL", "/media/")).rstrip("/")
        return f"{media_url}/{file_name}"

    return ""


class CustomUser(AbstractUser):
    name = models.CharField(max_length=150, blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)
    phone_number = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
    )
    email = models.EmailField(unique=True)
    profile_image = models.ImageField(
        upload_to="profiles/",
        null=True,
        blank=True,
    )
    course_access_approved = models.BooleanField(default=False)
    active_session_key = models.CharField(
        max_length=40,
        blank=True,
        default="",
    )

    @property
    def has_paid(self):
        from .models import Order
        return Order.objects.filter(
            user=self,
            paid=True,
        ).exists()

    @property
    def profile_image_url(self):
        if not self.profile_image:
            return static("default-profile.jpg")

        url = _public_file_url(self.profile_image)
        if not url:
            return static("default-profile.jpg")

        separator = "&" if "?" in url else "?"
        cache_version = quote(
            str(self.profile_image),
            safe="",
        )
        return f"{url}{separator}v={cache_version}"

    def __str__(self):
        return self.username


class AuthSession(models.Model):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="auth_sessions",
    )
    access_jti = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
    )
    refresh_jti = models.CharField(
        max_length=64,
        unique=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]


class ContactMessage(models.Model):
    sender = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="contact_messages",
        null=True,
        blank=True,
    )
    recipient = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="received_messages",
        null=True,
        blank=True,
    )
    sender_is_admin = models.BooleanField(
        default=False,
    )
    message = models.TextField(
        blank=True,
        default="",
    )
    image = models.ImageField(
        upload_to="contact_images/",
        null=True,
        blank=True,
    )
    video = models.FileField(
        upload_to="contact_videos/",
        null=True,
        blank=True,
    )
    is_read = models.BooleanField(
        default=False,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return (
            f"{self.sender or 'Anonymous'}: "
            f"{self.message[:40]}"
        )

    @property
    def image_url(self):
        return _public_file_url(self.image)

    @property
    def video_url(self):
        return _public_file_url(self.video)


class Course(models.Model):
    title = models.CharField(
        max_length=200,
    )
    description = models.TextField(
        blank=True,
        default="",
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    video = models.FileField(
        upload_to="course_videos/",
        null=True,
        blank=True,
    )
    video_url = models.URLField(
        blank=True,
        default="",
    )
    thumbnail = models.ImageField(
        upload_to="course_thumbnails/",
        null=True,
        blank=True,
    )
    instructor = models.CharField(
        max_length=200,
        blank=True,
        default="",
    )
    duration = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )
    level = models.CharField(
        max_length=20,
        choices=[
            ("Beginner", "Beginner"),
            ("Intermediate", "Intermediate"),
            ("Advanced", "Advanced"),
        ],
        default="Beginner",
    )
    is_active = models.BooleanField(
        default=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.video and not self.video_url:
            self.video_url = _public_file_url(self.video)
        super().save(*args, **kwargs)

    @property
    def video_public_url(self):
        return _public_file_url(self.video, self.video_url)

    @property
    def thumbnail_public_url(self):
        return _public_file_url(self.thumbnail)


class Module(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    title = models.CharField(
        max_length=200,
    )
    description = models.TextField(
        blank=True,
        default="",
    )
    video = models.FileField(
        upload_to="course_videos/",
        null=True,
        blank=True,
    )
    video_url = models.URLField(
        blank=True,
        default="",
    )
    order = models.IntegerField(
        default=0,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return (
            f"{self.course.title} - {self.title}"
        )

    def save(self, *args, **kwargs):
        if self.video and not self.video_url:
            self.video_url = _public_file_url(self.video)
        super().save(*args, **kwargs)

    @property
    def video_public_url(self):
        return _public_file_url(self.video, self.video_url)


class Lesson(models.Model):
    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    title = models.CharField(
        max_length=200,
    )
    description = models.TextField(
        blank=True,
        default="",
    )
    video = models.FileField(
        upload_to="course_videos/",
        null=True,
        blank=True,
    )
    video_url = models.URLField(
        blank=True,
        default="",
    )
    thumbnail = models.ImageField(
        upload_to="lesson_thumbnails/",
        null=True,
        blank=True,
    )
    thumbnail_url = models.URLField(
        blank=True,
        default="",
    )
    order = models.IntegerField(
        default=0,
    )
    subtitle_status = models.CharField(
        max_length=20,
        choices=[
            ("none", "None"),
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("ready", "Ready"),
            ("failed", "Failed"),
        ],
        default="none",
    )
    detected_language = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )
    detected_language_code = models.CharField(
        max_length=15,
        blank=True,
        default="",
    )
    subtitle_error = models.TextField(
        blank=True,
        default="",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return (
            f"{self.module.course.title} - "
            f"{self.module.title} - "
            f"{self.title}"
        )

    def save(self, *args, **kwargs):
        if self.video and not self.video_url:
            self.video_url = _public_file_url(self.video)
        if self.thumbnail and not self.thumbnail_url:
            self.thumbnail_url = _public_file_url(self.thumbnail)
        super().save(*args, **kwargs)

    @property
    def display_title(self):
        title = (self.title or "").strip()
        if not title:
            return f"Lesson {self.order}"
        # If title is a URL or file path, format a clean title
        if title.startswith(("http://", "https://", "course_videos/", "/media/")) or any(title.lower().endswith(ext) for ext in [".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"]):
            import os
            from urllib.parse import urlparse
            path = urlparse(title).path if title.startswith(("http://", "https://")) else title
            filename = os.path.basename(path)
            raw_name, _ = os.path.splitext(filename)
            if raw_name:
                formatted = raw_name.replace("_", " ").replace("-", " ").strip()
                if formatted.lower().startswith("lesson"):
                    return formatted.capitalize()
                return f"Lesson {self.order}: {formatted.title()}"
            return f"Lesson {self.order}"
        return title

    @property
    def video_public_url(self):
        return _public_file_url(self.video, self.video_url)

    @property
    def thumbnail_public_url(self):
        return _public_file_url(self.thumbnail, self.thumbnail_url)


class SubtitleTrack(models.Model):
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="subtitles",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="subtitles",
        null=True,
        blank=True,
    )
    language_code = models.CharField(
        max_length=15,
    )
    language_name = models.CharField(
        max_length=50,
    )
    is_original = models.BooleanField(
        default=False,
    )
    vtt_file = models.FileField(
        upload_to="subtitles/",
        null=True,
        blank=True,
    )
    srt_file = models.FileField(
        upload_to="subtitles/",
        null=True,
        blank=True,
    )
    vtt_content = models.TextField(
        blank=True,
        default="",
    )
    srt_content = models.TextField(
        blank=True,
        default="",
    )
    cues_data = models.JSONField(
        blank=True,
        default=list,
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("ready", "Ready"),
            ("failed", "Failed"),
        ],
        default="ready",
    )
    error_message = models.TextField(
        blank=True,
        default="",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["language_name"]
        unique_together = [("lesson", "language_code")]

    def __str__(self):
        parent = self.lesson.title if self.lesson else (self.course.title if self.course else "Unknown")
        return f"{parent} [{self.language_name}]"

    @property
    def vtt_public_url(self):
        from django.urls import reverse
        try:
            return reverse("serve_subtitle_vtt", args=[self.id])
        except Exception:
            return f"/subtitles/vtt/{self.id}/"

    @property
    def srt_public_url(self):
        from django.urls import reverse
        try:
            return reverse("serve_subtitle_srt", args=[self.id])
        except Exception:
            return f"/subtitles/srt/{self.id}/"


class SubtitleSetting(models.Model):
    key = models.CharField(
        max_length=100,
        unique=True,
    )
    value = models.JSONField(
        default=dict,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return self.key


class Order(models.Model):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    product_name = models.CharField(
        max_length=200,
        default="The AI Income Playbook",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    currency = models.CharField(
        max_length=10,
        default="USD",
    )
    razorpay_order_id = models.CharField(
        max_length=100,
        unique=True,
    )
    razorpay_payment_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )
    razorpay_signature = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    paid = models.BooleanField(
        default=False,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def save(self, *args, **kwargs):
        is_paid = bool(self.paid)

        super().save(*args, **kwargs)

        if is_paid and self.user_id is not None:
            user = self.user

            if (
                user is not None
                and not user.course_access_approved
            ):
                user.course_access_approved = True

                user.save(
                    update_fields=[
                        "course_access_approved"
                    ]
                )

    def __str__(self):
        return (
            f"{self.product_name} - "
            f"{self.razorpay_order_id}"
        )