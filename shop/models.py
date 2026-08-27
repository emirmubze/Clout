from django.db import models
from django.contrib.auth.models import AbstractUser


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
    profile_image = models.ImageField(upload_to="profiles/", null=True, blank=True)
    course_access_approved = models.BooleanField(default=False)
    active_session_key = models.CharField(max_length=40, blank=True, default="")

    @property
    def has_paid(self):
        from .models import Order
        return Order.objects.filter(user=self, paid=True).exists()

    def __str__(self):
        return self.username


class AuthSession(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="auth_sessions")
    refresh_jti = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

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
    sender_is_admin = models.BooleanField(default=False)
    message = models.TextField(blank=True, default="")
    image = models.ImageField(upload_to="contact_images/", null=True, blank=True)
    video = models.FileField(upload_to="contact_videos/", null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender or 'Anonymous'}: {self.message[:40]}"


class Course(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    video = models.FileField(upload_to="course_videos/", null=True, blank=True)
    video_url = models.URLField(blank=True, default="")
    thumbnail = models.ImageField(upload_to="course_thumbnails/", null=True, blank=True)
    instructor = models.CharField(max_length=200, blank=True, default="")
    duration = models.CharField(max_length=50, blank=True, default="")
    level = models.CharField(
        max_length=20,
        choices=[
            ('Beginner', 'Beginner'),
            ('Intermediate', 'Intermediate'),
            ('Advanced', 'Advanced'),
        ],
        default='Beginner'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Module(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    video = models.FileField(upload_to="course_videos/", null=True, blank=True)
    order = models.IntegerField(default=0)  # For ordering modules
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.course.title} - {self.title}"


class Lesson(models.Model):
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    video = models.FileField(upload_to="course_videos/", null=True, blank=True)
    thumbnail = models.ImageField(upload_to="lesson_thumbnails/", null=True, blank=True)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.module.course.title} - {self.module.title} - {self.title}"


class Order(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, null=True, blank=True)
    product_name = models.CharField(max_length=200, default="The AI Income Playbook")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    razorpay_order_id = models.CharField(max_length=100, unique=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        is_paid = bool(self.paid)
        super().save(*args, **kwargs)

        if is_paid and self.user_id is not None:
            user = self.user
            if user is not None and not user.course_access_approved:
                user.course_access_approved = True
                user.save(update_fields=["course_access_approved"])

    def __str__(self):
        return f"{self.product_name} - {self.razorpay_order_id}"
