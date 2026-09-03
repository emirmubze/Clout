from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from urllib.parse import quote


def _public_file_url(file_field, explicit_url=""):
    url = str(explicit_url or "").strip()
    if url.startswith(("http://", "https://")):
        return url

    if not file_field:
        return ""

    custom_domain = str(
        getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "")
    ).strip().rstrip("/")
    file_name = str(getattr(file_field, "name", "")).lstrip("/")
    if custom_domain and file_name:
        return f"https://{custom_domain}/{file_name}"

    try:
        return str(file_field.url or "")
    except (AttributeError, ValueError):
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
            return ""

        try:
            image_url = self.profile_image.url
            separator = "&" if "?" in image_url else "?"
            cache_version = quote(
                str(self.profile_image),
                safe="",
            )
            return f"{image_url}{separator}v={cache_version}"

        except (AttributeError, ValueError):
            domain = getattr(
                settings,
                "AWS_S3_CUSTOM_DOMAIN",
                "",
            )

            if not domain:
                return ""

            relative_path = str(
                self.profile_image
            ).lstrip("/")

            return (
                f"https://{domain.rstrip('/')}"
                f"/{relative_path}"
            )

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

    @property
    def video_public_url(self):
        """
        Return a browser-playable video URL.

        Priority:
        1. Explicit public R2 URL in video_url
        2. Django storage URL from video
        """

        return _public_file_url(self.video, self.video_url)

    @property
    def thumbnail_public_url(self):
        if self.thumbnail_url:
            url = str(
                self.thumbnail_url
            ).strip()

            if url.startswith(
                ("http://", "https://")
            ):
                return url

        if not self.thumbnail:
            return ""

        try:
            url = self.thumbnail.url

            if url:
                return str(url)

        except (AttributeError, ValueError):
            pass

        return ""


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