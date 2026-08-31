from django.urls import path
from django.contrib.auth import views as auth_views

from . import views
from . import auth_api


urlpatterns = [

    path("api/users/", auth_api.register, name="api_register"),
    path("api/users/me/", auth_api.me, name="api_me"),
    path("api/users/me/password/", auth_api.change_password, name="api_change_password"),
    path("api/sessions/", auth_api.login, name="api_login"),
    path("api/token/", auth_api.refresh, name="api_refresh"),
    path("api/logout/", auth_api.logout, name="api_logout"),
    path("api/logout/all/", auth_api.logout_all, name="api_logout_all"),
    path("api/admin-dashboard/", auth_api.admin_dashboard, name="api_admin_dashboard"),

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

    path(
        "admin-user-add/",
        views.admin_user_add,
        name="admin_user_add"
    ),

    path(
        "admin-user-delete/<int:user_id>/",
        views.admin_user_delete,
        name="admin_user_delete"
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

    path(
        "admin-modules-save/",
        views.admin_modules_save,
        name="admin_modules_save"
    ),

    path(
        "admin-r2-presign/",
        views.r2_presign_upload,
        name="r2_presign_upload"
    ),

    path(
        "lesson-video/<int:lesson_id>/",
        views.serve_lesson_video,
        name="serve_lesson_video"
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