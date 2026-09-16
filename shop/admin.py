from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import Order, CustomUser, Course, ContactMessage, Module, PromotionalBanner


class CustomUserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Custom Fields', {'fields': ('name', 'age', 'phone_number', 'profile_image', 'course_access_approved')}),
    )
    list_display = ('username', 'email', 'name', 'phone_number', 'profile_image', 'is_staff')
    list_filter = BaseUserAdmin.list_filter + ('age', 'course_access_approved')


admin.site.register(CustomUser, CustomUserAdmin)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'product_name', 'amount', 'paid', 'created_at')
    list_filter = ('paid', 'created_at', 'currency')
    search_fields = ('user__email', 'razorpay_order_id', 'product_name')
    readonly_fields = ('razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature', 'created_at')
    fieldsets = (
        ('Order Info', {'fields': ('user', 'product_name', 'amount', 'currency', 'paid')}),
        ('Razorpay Details', {'fields': ('razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature')}),
        ('Timestamps', {'fields': ('created_at',)}),
    )


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'instructor', 'level', 'price', 'is_active', 'created_at')
    list_filter = ('level', 'is_active', 'created_at')
    search_fields = ('title', 'instructor', 'description')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Course Info', {'fields': ('title', 'description', 'instructor', 'level')}),
        ('Media', {'fields': ('video', 'video_url', 'thumbnail')}),
        ('Details', {'fields': ('price', 'duration', 'is_active')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'order', 'created_at')
    list_filter = ('course', 'created_at')
    search_fields = ('title', 'description', 'course__title')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Module Info', {'fields': ('course', 'title', 'description', 'order')}),
        ('Media', {'fields': ('video',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'recipient', 'sender_is_admin', 'is_read', 'created_at')
    list_filter = ('sender_is_admin', 'is_read', 'created_at')
    search_fields = ('sender__email', 'recipient__email', 'message')
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Sender/Recipient', {'fields': ('sender', 'recipient', 'sender_is_admin')}),
        ('Message', {'fields': ('message', 'image', 'video')}),
        ('Status', {'fields': ('is_read',)}),
        ('Timestamps', {'fields': ('created_at',)}),
    )


@admin.register(PromotionalBanner)
class PromotionalBannerAdmin(admin.ModelAdmin):
    list_display = ('title', 'thumbnail_preview', 'link_url', 'order', 'is_active', 'created_at')
    list_editable = ('order', 'is_active')
    list_filter = ('is_active', 'created_at')
    search_fields = ('title', 'link_url')
    readonly_fields = ('created_at', 'updated_at', 'thumbnail_preview')
    fieldsets = (
        ('Banner Info', {'fields': ('title', 'link_url', 'order', 'is_active')}),
        ('Image', {'fields': ('image', 'image_url', 'thumbnail_preview')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    def thumbnail_preview(self, obj):
        url = obj.image_public_url
        if url:
            return format_html('<img src="{}" style="max-height: 48px; max-width: 120px; border-radius: 4px; object-fit: cover;" />', url)
        return "-"
    thumbnail_preview.short_description = "Preview"
