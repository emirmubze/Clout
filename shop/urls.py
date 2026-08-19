from django.urls import path
from django.contrib.auth import views as auth_views

from . import views


urlpatterns = [

    # =====================================================
    # HOME
    # =====================================================

    path(
        "",
        views.index,
        name="index"
    ),

    # =====================================================
    # COURSE
    # =====================================================

    path(
        "course/",
        views.course_detail,
        name="course_detail"
    ),

    path(
        "my-course/",
        views.course,
        name="course"
    ),

    # =====================================================
    # CONTACT
    # =====================================================

    path(
        "contact/",
        views.contact,
        name="contact"
    ),

    path(
        "api/chat/",
        views.ai_chat,
        name="ai_chat"
    ),

    path(
        "admin-send-message/<int:user_id>/",
        views.admin_send_message,
        name="admin_send_message"
    ),

    path(
        "toggle-course-access/<int:user_id>/",
        views.toggle_course_access,
        name="toggle_course_access"
    ),

    # =====================================================
    # ADMIN DASHBOARD
    # =====================================================

    path(
        "admin-dashboard/",
        views.admin_dashboard,
        name="admin_dashboard"
    ),

    path(
        "admin-course-add/",
        views.admin_course_add,
        name="admin_course_add"
    ),

    path(
        "admin-course-edit/<int:course_id>/",
        views.admin_course_edit,
        name="admin_course_edit"
    ),

    path(
        "admin-course-delete/<int:course_id>/",
        views.admin_course_delete,
        name="admin_course_delete"
    ),

    # =====================================================
    # LOGIN
    # =====================================================

    path(
        "login/",
        views.SingleDeviceLoginView.as_view(
            template_name="shop/signin.html"
        ),
        name="login"
    ),

    path(
        "course-video/<int:course_id>/",
        views.serve_course_video,
        name="course_video"
    ),

    path(
        "module-video/<int:module_id>/",
        views.serve_module_video,
        name="module_video"
    ),

    path(
        "message-video/<int:message_id>/",
        views.serve_message_video,
        name="message_video"
    ),

    # =====================================================
    # REGISTER
    # =====================================================

    path(
        "register/",
        views.register_view,
        name="register"
    ),

    # =====================================================
    # LOGOUT
    # =====================================================

    path(
        "logout/",
        views.logout_view,
        name="logout"
    ),

    # =====================================================
    # PROFILE
    # =====================================================

    path(
        "profile/",
        views.profile,
        name="profile"
    ),

    # =====================================================
    # CHECKOUT
    # =====================================================

    path(
        "checkout/",
        views.checkout,
        name="checkout"
    ),

    # =====================================================
    # RAZORPAY
    # =====================================================

    path(
        "create-order/",
        views.create_order,
        name="create_order"
    ),

    path(
        "verify-payment/",
        views.verify_payment,
        name="verify_payment"
    ),

    # =====================================================
    # PAYMENT SUCCESS
    # =====================================================

    path(
        "payment-success/",
        views.payment_success,
        name="payment_success"
    ),

    # =====================================================
    # PASSWORD RESET
    # =====================================================

    path(
        "forgot-password/",
        views.forgot_password,
        name="forgot_password"
    ),

    path(
        "reset-link-sent/",
        views.reset_link_sent,
        name="reset_link_sent"
    ),

    path(
        "reset-password/<uidb64>/<token>/",
        views.reset_password,
        name="reset_password"
    ),

    path(
        "password-reset-success/",
        views.password_reset_success,
        name="password_reset_success"
    ),
]