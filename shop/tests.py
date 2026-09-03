import json
import os
from unittest.mock import patch
from smtplib import SMTPException

from django.core import mail
from django.core.management import call_command
from django.contrib.auth.tokens import default_token_generator
from django.middleware.csrf import get_token
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import CustomUser, ContactMessage, Course, Module, Lesson, Order


class UserAuthAndDashboardTests(TestCase):
    @patch.dict(
        os.environ,
        {
            "ADMIN_USERNAME": "",
            "ADMIN_EMAIL": "",
            "ADMIN_PASSWORD": "",
        },
    )
    def test_ensure_admin_requires_credentials(self):
        call_command("ensure_admin")
        self.assertFalse(CustomUser.objects.filter(is_superuser=True).exists())


    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_forgot_password_sends_reset_email(self):
        user = CustomUser.objects.create_user(
            username="resetuser",
            email="reset@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("forgot_password"),
            {"email": user.email},
        )

        self.assertRedirects(response, reverse("reset_link_sent"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/reset-password/", mail.outbox[0].body)
        self.assertIn("clout.courses", mail.outbox[0].from_email)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_forgot_password_supports_username_lookup(self):
        user = CustomUser.objects.create_user(
            username="lookupbyusername",
            email="lookupbyusername@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("forgot_password"),
            {"email": "lookupbyusername"},
        )

        self.assertRedirects(response, reverse("reset_link_sent"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(user.email, mail.outbox[0].to)
        self.assertIn("clout.courses", mail.outbox[0].from_email)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_forgot_password_rejects_unknown_email_without_sending_email(self):
        response = self.client.post(
            reverse("forgot_password"),
            {"email": " unknown@example.com "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "shop/forgot-password.html")
        self.assertContains(response, "No account found with this email address.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(CustomUser.objects.filter(email="unknown@example.com").exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_forgot_password_matches_registered_email_case_insensitively(self):
        user = CustomUser.objects.create_user(
            username="caseuser",
            email="case@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("forgot_password"),
            {"email": "  CASE@EXAMPLE.COM  "},
        )

        self.assertRedirects(response, reverse("reset_link_sent"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(user.email, mail.outbox[0].to)

    def test_reset_password_rejects_inactive_user_token(self):
        user = CustomUser.objects.create_user(
            username="inactiveuser",
            email="inactive@example.com",
            password="StrongPass123!",
            is_active=False,
        )
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        response = self.client.get(
            reverse("reset_password", kwargs={"uidb64": uid, "token": token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This reset link is invalid or has expired.")

    @patch("shop.views.send_mail", side_effect=SMTPException("SMTP unavailable"))
    def test_forgot_password_does_not_error_when_email_fails(self, send_mail_mock):
        user = CustomUser.objects.create_user(
            username="failedresetuser",
            email="failed-reset@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("forgot_password"),
            {"email": user.email},
        )

        self.assertRedirects(response, reverse("reset_link_sent"))
        send_mail_mock.assert_called_once()

    def test_registration_form_saves_username_and_name(self):
        form_data = {
            "username": "aliceuser",
            "name": "Alice Johnson",
            "email": "alice@example.com",
            "phone_number": "9876543210",
            "age": 24,
            "password1": "VeryStrongPass123!",
            "password2": "VeryStrongPass123!",
        }

        response = self.client.post(reverse("register"), form_data)

        self.assertEqual(response.status_code, 302)
        user = CustomUser.objects.get(email="alice@example.com")
        self.assertEqual(user.username, "aliceuser")
        self.assertEqual(user.name, "Alice Johnson")

    def test_registration_rejects_malformed_email(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "invalidemailuser",
                "name": "Invalid Email",
                "email": "not-an-email",
                "phone_number": "9876543210",
                "age": 24,
                "password1": "VeryStrongPass123!",
                "password2": "VeryStrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid email address.")
        self.assertFalse(CustomUser.objects.filter(username="invalidemailuser").exists())

    def test_registration_rejects_malformed_phone_number(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "invalidphoneuser",
                "name": "Invalid Phone",
                "email": "valid-phone-email@example.com",
                "country_code": "+91",
                "phone": "12abc",
                "age": 24,
                "password1": "VeryStrongPass123!",
                "password2": "VeryStrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid phone number.")
        self.assertFalse(CustomUser.objects.filter(username="invalidphoneuser").exists())

    def test_signup_browser_field_names_create_user(self):
        form_data = {
            "username": "browseruser",
            "name": "Browser User",
            "email": "browser@example.com",
            "country_code": "+91",
            "phone": "9876543210",
            "password": "VeryStrongPass123!",
            "confirm_password": "VeryStrongPass123!",
        }

        response = self.client.post(reverse("register"), form_data)

        self.assertEqual(response.status_code, 302)
        user = CustomUser.objects.get(email="browser@example.com")
        self.assertEqual(user.username, "browseruser")
        self.assertEqual(user.name, "Browser User")
        self.assertEqual(user.phone_number, "+91 9876543210")

    def test_signup_accepts_trusted_render_origin_csrf(self):
        client = Client(enforce_csrf_checks=True)

        response = client.get(reverse("register"))
        csrf_token = get_token(response.wsgi_request)

        form_data = {
            "username": "renderuser",
            "name": "Render User",
            "email": "renderuser@example.com",
            "country_code": "+91",
            "phone": "9876543210",
            "password": "VeryStrongPass123!",
            "confirm_password": "VeryStrongPass123!",
            "csrfmiddlewaretoken": csrf_token,
        }

        response = client.post(
            reverse("register"),
            form_data,
            HTTP_ORIGIN="https://clout.onrender.com",
        )

        self.assertEqual(response.status_code, 302)
        user = CustomUser.objects.get(email="renderuser@example.com")
        self.assertEqual(user.username, "renderuser")
        self.assertEqual(user.phone_number, "+91 9876543210")

    def test_session_mismatch_does_not_logout_logged_in_user(self):
        user = CustomUser.objects.create_user(
            username="sessionuser",
            email="sessionuser@example.com",
            password="VeryStrongPass123!",
            name="Session User",
        )

        client = Client()
        client.force_login(user)

        user.active_session_key = "stale-session-key"
        user.save(update_fields=["active_session_key"])

        response = client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user, user)

    def test_login_accepts_email_and_password(self):
        user = CustomUser.objects.create_user(
            username="aliceuser",
            email="alice@example.com",
            password="VeryStrongPass123!",
            name="Alice Johnson",
        )

        response = self.client.post(
            reverse("login"),
            {"username": "alice@example.com", "password": "VeryStrongPass123!"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user, user)

    def test_superuser_login_returns_to_admin_dashboard(self):
        user = CustomUser.objects.create_superuser(
            username="superuser",
            email="superuser@example.com",
            password="VeryStrongPass123!",
        )

        response = self.client.post(
            reverse("login"),
            {"username": "superuser", "password": "VeryStrongPass123!"},
        )

        self.assertRedirects(response, reverse("admin_dashboard"))
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)

    def test_superuser_login_accepts_pasted_username(self):
        user = CustomUser.objects.create_superuser(
            username="pastedadmin",
            email="pastedadmin@example.com",
            password="VeryStrongPass123!",
        )

        response = self.client.post(
            reverse("login"),
            {"username": "  pastedadmin  ", "password": "VeryStrongPass123!"},
        )

        self.assertRedirects(response, reverse("admin_dashboard"))
        self.assertEqual(response.wsgi_request.user, user)

    def test_admin_login_uses_shared_login_page(self):
        response = self.client.get("/admin/login/?next=/admin/")

        self.assertRedirects(response, "/login/?next=%2Fadmin%2F")

    def test_login_shows_error_for_invalid_credentials(self):
        CustomUser.objects.create_user(
            username="aliceuser",
            email="alice@example.com",
            password="VeryStrongPass123!",
            name="Alice Johnson",
        )

        response = self.client.post(
            reverse("login"),
            {"username": "aliceuser", "password": "WrongPassword123!"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct username and password")

    @patch("shop.views.logout", side_effect=RuntimeError("session unavailable"))
    def test_logout_failure_redirects_with_only_an_inline_error(self, logout_mock):
        user = CustomUser.objects.create_user(
            username="logoutfailure",
            email="logout-failure@example.com",
            password="VeryStrongPass123!",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("logout"))

        self.assertRedirects(response, f"{reverse('login')}?logout_error=1")
        login_response = self.client.get(response.url)
        self.assertContains(login_response, "Unable to log out. Please try again.")
        self.assertContains(login_response, "Log In")

    def test_latest_login_invalidates_previous_device_session(self):
        user = CustomUser.objects.create_user(
            username="multiuser",
            email="multi@example.com",
            password="VeryStrongPass123!",
        )
        device_one = Client()
        device_two = Client()

        first_login = device_one.post(
            reverse("login"),
            {
                "username": "multiuser",
                "password": "VeryStrongPass123!",
            },
        )
        self.assertEqual(first_login.status_code, 302)
        first_session_key = device_one.session.session_key

        second_login = device_two.post(
            reverse("login"),
            {
                "username": "multiuser",
                "password": "VeryStrongPass123!",
            },
        )
        self.assertEqual(second_login.status_code, 302)
        self.assertNotEqual(first_session_key, device_two.session.session_key)

        response = device_one.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertEqual(
            device_two.get(reverse("index")).wsgi_request.user,
            user,
        )

    def test_contact_messages_are_saved_and_visible_in_admin_dashboard(self):
        customer = CustomUser.objects.create_user(
            username="customeruser",
            email="customer@example.com",
            password="StrongPass123!",
            name="Customer User",
        )
        admin = CustomUser.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="StrongPass123!",
            name="Admin User",
            is_staff=True,
        )

        self.client.force_login(customer)
        response = self.client.post(reverse("contact"), {"message": "I need help with my course"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(customer.contact_messages.filter(message="I need help with my course").exists())

        self.client.force_login(admin)
        dashboard_response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Customer User")
        self.assertContains(dashboard_response, "I need help with my course")

    def test_contact_ajax_message_returns_saved_message(self):
        customer = CustomUser.objects.create_user(
            username="ajaxcustomer",
            email="ajaxcustomer@example.com",
            password="StrongPass123!",
        )

        self.client.force_login(customer)
        response = self.client.post(
            reverse("contact"),
            {"message": "Instant message"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "Instant message")
        self.assertTrue(
            ContactMessage.objects.filter(
                sender=customer,
                message="Instant message",
            ).exists()
        )

    def test_contact_ajax_poll_returns_new_admin_reply(self):
        customer = CustomUser.objects.create_user(
            username="pollcustomer",
            email="pollcustomer@example.com",
            password="StrongPass123!",
        )
        admin = CustomUser.objects.create_user(
            username="polladmin",
            email="polladmin@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        reply = ContactMessage.objects.create(
            sender=admin,
            recipient=customer,
            sender_is_admin=True,
            message="New support reply",
        )

        self.client.force_login(customer)
        response = self.client.get(
            reverse("contact") + "?after=0",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["messages"][0]["id"], reply.id)
        self.assertEqual(response.json()["messages"][0]["message"], "New support reply")

    def test_contact_messages_are_isolated_between_users(self):
        user_a = CustomUser.objects.create_user(
            username="contactusera",
            email="contacta@example.com",
            password="StrongPass123!",
        )
        user_b = CustomUser.objects.create_user(
            username="contactuserb",
            email="contactb@example.com",
            password="StrongPass123!",
        )
        ContactMessage.objects.create(
            sender=user_a,
            message="Private message for A",
        )

        self.client.force_login(user_b)
        response = self.client.get(reverse("contact"))

        self.assertNotContains(response, "Private message for A")
        self.assertContains(response, "Start a conversation with CLOUT Support.")

    def test_admin_ajax_conversation_returns_only_selected_user_messages(self):
        admin = CustomUser.objects.create_user(
            username="ajaxadmin",
            email="ajaxadmin@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        user_a = CustomUser.objects.create_user(
            username="ajaxusera",
            email="ajaxa@example.com",
            password="StrongPass123!",
        )
        user_b = CustomUser.objects.create_user(
            username="ajaxuserb",
            email="ajaxb@example.com",
            password="StrongPass123!",
        )
        ContactMessage.objects.create(sender=user_a, message="A message")
        ContactMessage.objects.create(sender=user_b, message="B message")

        self.client.force_login(admin)
        response = self.client.get(
            reverse("admin_dashboard") + f"?user_id={user_a.id}",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [message["message"] for message in response.json()["messages"]],
            ["A message"],
        )

    def test_user_can_log_in_after_logout(self):
        user = CustomUser.objects.create_user(
            username="logoutuser",
            email="logoutuser@example.com",
            password="StrongPass123!",
            name="Logout User",
        )

        self.client.login(username="logoutuser", password="StrongPass123!")
        self.client.get(reverse("logout"))

        login_response = self.client.post(
            reverse("login"),
            {"username": "logoutuser@example.com", "password": "StrongPass123!"},
            follow=True,
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertTrue(login_response.wsgi_request.user.is_authenticated)
        self.assertEqual(login_response.wsgi_request.user, user)

    def test_paid_order_auto_approves_course_access(self):
        user = CustomUser.objects.create_user(
            username="paiduser",
            email="paiduser@example.com",
            password="StrongPass123!",
            name="Paid User",
        )

        Order.objects.create(
            user=user,
            amount=18.82,
            currency="USD",
            razorpay_order_id="order_paid_123",
            paid=True,
        )

        user.refresh_from_db()
        self.assertTrue(user.course_access_approved)

        self.client.force_login(user)
        course_response = self.client.get(reverse("course_detail"))
        self.assertEqual(course_response.status_code, 200)

    def test_saving_course_modules_preserves_existing_records_and_access(self):
        admin = CustomUser.objects.create_user(
            username="contentadmin",
            email="contentadmin@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        user = CustomUser.objects.create_user(
            username="existingbuyer",
            email="existingbuyer@example.com",
            password="StrongPass123!",
        )
        Order.objects.create(
            user=user,
            amount=18.82,
            currency="USD",
            razorpay_order_id="order_existing_123",
            paid=True,
        )
        course = Course.objects.create(title="Existing Course")
        module = Module.objects.create(course=course, title="Original Module", order=1)
        lesson = Lesson.objects.create(module=module, title="Original Lesson", order=1)

        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin_modules_save"),
            {
                "module_count": "1",
                "module_id_0": str(module.id),
                "module_title_0": "Updated Module",
                "module_description_0": "Updated description",
                "module_lesson_count_0": "1",
                "module_0_lesson_id_0": str(lesson.id),
                "module_0_lesson_title_0": "Updated Lesson",
                "module_0_lesson_description_0": "Updated lesson description",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Module.objects.get(pk=module.pk).title, "Updated Module")
        self.assertEqual(Lesson.objects.get(pk=lesson.pk).title, "Updated Lesson")
        self.assertTrue(Order.objects.get(razorpay_order_id="order_existing_123").paid)
        user.refresh_from_db()
        self.assertTrue(user.course_access_approved)

    def test_saving_empty_module_creates_no_lessons(self):
        admin = CustomUser.objects.create_user(
            username="emptyadmin",
            email="emptyadmin@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        course = Course.objects.create(title="Empty Test Course")
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin_modules_save"),
            {
                "module_count": "1",
                "module_id_0": "",
                "module_title_0": "Empty New Module",
                "module_description_0": "",
                "module_lesson_count_0": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        module = Module.objects.filter(title="Empty New Module").first()
        self.assertIsNotNone(module)
        self.assertEqual(module.lessons.count(), 0)

    def test_admin_modules_save_returns_updated_modules_and_preserves_assets(self):
        admin = CustomUser.objects.create_user(
            username="modulesaver",
            email="modulesaver@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        course = Course.objects.create(title="Curriculum Test Course", is_active=True)
        module = Module.objects.create(course=course, title="Module Alpha", order=1)
        lesson = Lesson.objects.create(
            module=module,
            title="Lesson Alpha 1",
            video_url="https://example.com/video1.mp4",
            thumbnail_url="https://example.com/thumb1.jpg",
            order=1,
        )

        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin_modules_save"),
            {
                "course_id": str(course.id),
                "module_count": "1",
                "module_id_0": str(module.id),
                "module_title_0": "Module Alpha Renamed",
                "module_description_0": "Module description",
                "module_lesson_count_0": "1",
                "module_0_lesson_id_0": str(lesson.id),
                "module_0_lesson_title_0": "Lesson Alpha Renamed",
                "module_0_lesson_description_0": "Lesson description",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["course_id"], course.id)
        self.assertEqual(len(data["modules"]), 1)
        self.assertEqual(data["modules"][0]["title"], "Module Alpha Renamed")
        self.assertEqual(len(data["modules"][0]["lessons"]), 1)
        self.assertEqual(data["modules"][0]["lessons"][0]["title"], "Lesson Alpha Renamed")
        self.assertEqual(data["modules"][0]["lessons"][0]["video_url"], "https://example.com/video1.mp4")
        self.assertEqual(data["modules"][0]["lessons"][0]["thumbnail_url"], "https://example.com/thumb1.jpg")

        lesson.refresh_from_db()
        self.assertEqual(lesson.title, "Lesson Alpha Renamed")
        self.assertEqual(lesson.video_url, "https://example.com/video1.mp4")
        self.assertEqual(lesson.thumbnail_url, "https://example.com/thumb1.jpg")

    def test_admin_dashboard_renders_course_modules_and_lessons(self):
        admin = CustomUser.objects.create_user(
            username="dashboardadmin",
            email="dashboardadmin@example.com",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        course = Course.objects.create(title="Active Dashboard Course", is_active=True)
        module = Module.objects.create(course=course, title="Dashboard Module 1", order=1)
        Lesson.objects.create(module=module, title="Dashboard Lesson 1", order=1)

        self.client.force_login(admin)
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard Module 1")
        self.assertContains(response, "Dashboard Lesson 1")
        self.assertEqual(len(response.context["course_modules"]), 1)
        self.assertEqual(len(response.context["course_modules"][0]["lessons"]), 1)

    def test_empty_module_renders_empty_state_in_course_view(self):
        user = CustomUser.objects.create_user(
            username="courseviewer",
            email="courseviewer@example.com",
            password="StrongPass123!",
            is_superuser=True,
        )
        course = Course.objects.create(title="Active Empty Course", is_active=True)
        Module.objects.create(course=course, title="Module Without Lessons", order=1)

        self.client.force_login(user)
        response = self.client.get(reverse("course"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Module Without Lessons")
        self.assertContains(response, "No lessons added yet")
        self.assertContains(response, "0 lessons")

    def test_admin_can_toggle_free_course_access(self):
        admin = CustomUser.objects.create_user(
            username="adminuser2",
            email="adminuser2@example.com",
            password="StrongPass123!",
            name="Admin User 2",
            is_staff=True,
        )
        user = CustomUser.objects.create_user(
            username="freeaccessuser",
            email="freeaccessuser@example.com",
            password="StrongPass123!",
            name="Free Access User",
        )

        self.client.force_login(admin)
        response = self.client.post(reverse("toggle_course_access", args=[user.id]))

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.course_access_approved)

        self.client.force_login(user)
        course_response = self.client.get(reverse("course_detail"))
        self.assertEqual(course_response.status_code, 200)

    def test_admin_can_add_user_via_ajax(self):
        admin = CustomUser.objects.create_user(
            username="adminaddtester",
            email="adminaddtester@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin_user_add"),
            {
                "name": "New Test User",
                "username": "newtestuser",
                "email": "newtestuser@example.com",
                "phone_number": "+91 9988776655",
                "password": "StrongPass123!",
                "course_access_approved": "true",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["user"]["username"], "newtestuser")
        self.assertTrue(data["user"]["course_access_approved"])
        self.assertTrue(CustomUser.objects.filter(username="newtestuser").exists())

    def test_admin_can_delete_user_via_ajax(self):
        admin = CustomUser.objects.create_user(
            username="admindeltester",
            email="admindeltester@example.com",
            password="StrongPass123!",
            is_staff=True,
        )
        target = CustomUser.objects.create_user(
            username="targetdeleteuser",
            email="targetdelete@example.com",
            password="StrongPass123!",
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin_user_delete", args=[target.id]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(CustomUser.objects.filter(id=target.id).exists())

    def test_course_detail_excludes_removed_intro_and_purchase_card(self):
        response = self.client.get(reverse("course_detail"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="course-intro"')
        self.assertNotContains(response, "This beginner-friendly program")
        self.assertNotContains(response, 'class="card-buy"')
        self.assertContains(response, "6 sections | 22 modules")
        self.assertContains(response, "Module 1:")
        self.assertContains(response, "AI Income Foundations The Landscape")

    def test_index_course_cta_uses_paid_or_admin_approved_access(self):
        from .models import Order as OrderModel

        paid_user = CustomUser.objects.create_user(
            username="paidctauser",
            email="paidcta@example.com",
            password="StrongPass123!",
            name="Paid CTA User",
        )
        OrderModel.objects.create(
            user=paid_user,
            amount=18.82,
            currency="USD",
            razorpay_order_id="cta_paid_order",
            paid=True,
        )

        approved_user = CustomUser.objects.create_user(
            username="approvedctauser",
            email="approvedcta@example.com",
            password="StrongPass123!",
            name="Approved CTA User",
        )
        approved_user.course_access_approved = True
        approved_user.save(update_fields=["course_access_approved"])

        locked_user = CustomUser.objects.create_user(
            username="lockedctauser",
            email="lockedcta@example.com",
            password="StrongPass123!",
            name="Locked CTA User",
        )

        for user in [paid_user, approved_user]:
            self.client.force_login(user)
            response = self.client.get(reverse("index"))
            self.assertContains(response, reverse("course"))

        self.client.force_login(locked_user)
        locked_response = self.client.get(reverse("index"))
        self.assertContains(locked_response, reverse("checkout"))

    def test_admin_dashboard_shows_user_name_and_username(self):
        staff = CustomUser.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="StrongPass123!",
            name="Admin User",
            is_staff=True,
        )
        CustomUser.objects.create_user(
            username="customeruser",
            email="customer@example.com",
            password="StrongPass123!",
            name="Customer User",
        )

        self.client.force_login(staff)
        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin User")
        self.assertContains(response, "adminuser")
        self.assertContains(response, "Customer User")

    def test_admin_dashboard_includes_course_payment_stats(self):
        from .models import Order as OrderModel

        staff = CustomUser.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="StrongPass123!",
            name="Admin User",
            is_staff=True,
        )
        user = CustomUser.objects.create_user(
            username="paiduser",
            email="paiduser@example.com",
            password="StrongPass123!",
            name="Paid User",
        )
        OrderModel.objects.create(
            user=user,
            amount=18.82,
            currency="USD",
            razorpay_order_id="order_paid_abc",
            paid=True,
        )

        self.client.force_login(staff)
        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_payments"], 1)
        self.assertEqual(response.context["course_buyers"], 1)
        self.assertTrue(response.context["users"].get(id=user.id).has_paid)

    def test_admin_can_add_and_edit_course(self):
        admin = CustomUser.objects.create_user(
            username="admincourse",
            email="admincourse@example.com",
            password="StrongPass123!",
            name="Admin Course",
            is_staff=True,
        )

        self.client.force_login(admin)

        add_response = self.client.post(
            reverse("admin_course_add"),
            {
                "title": "AI Growth Blueprint",
                "description": "Learn growth systems.",
                "price": "49.00",
                "instructor": "Jane",
                "duration": "4 weeks",
                "level": "Beginner",
                "is_active": "on",
            },
        )
        self.assertEqual(add_response.status_code, 302)
        course = Course.objects.get(title="AI Growth Blueprint")
        self.assertEqual(course.price, 49.00)

        edit_response = self.client.post(
            reverse("admin_course_edit", args=[course.id]),
            {
                "title": "AI Growth Blueprint Pro",
                "description": "Updated growth systems.",
                "price": "59.00",
                "instructor": "Jane Doe",
                "duration": "6 weeks",
                "level": "Intermediate",
                "is_active": "on",
            },
        )
        self.assertEqual(edit_response.status_code, 302)
        course.refresh_from_db()
        self.assertEqual(course.title, "AI Growth Blueprint Pro")
        self.assertEqual(course.price, 59.00)

    def test_message_notification_badge_shows_only_with_unread_admin_messages(self):

        user = CustomUser.objects.create_user(
            username="userwithnomessages",
            email="user@example.com",
            password="StrongPass123!",
            name="User",
        )
        admin = CustomUser.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="StrongPass123!",
            name="Admin",
            is_staff=True,
        )

        # User not logged in: no badge
        response = self.client.get(reverse("index"))
        self.assertFalse(response.context["has_unread_messages"])

        # User logged in with no unread messages: no badge
        self.client.force_login(user)
        response = self.client.get(reverse("index"))
        self.assertFalse(response.context["has_unread_messages"])

        # Admin sends a message (unread): badge should show
        ContactMessage.objects.create(
            sender=admin,
            sender_is_admin=True,
            message="Admin reply",
            is_read=False,
        )
        response = self.client.get(reverse("index"))
        self.assertTrue(response.context["has_unread_messages"])

        # User visits contact page: messages are marked as read, badge should hide
        response = self.client.get(reverse("contact"))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("index"))
        self.assertFalse(response.context["has_unread_messages"])

    def test_contact_message_with_image_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        from io import BytesIO

        user = CustomUser.objects.create_user(
            username="imageuser",
            email="imageuser@example.com",
            password="StrongPass123!",
            name="Image User",
        )

        self.client.force_login(user)

        # Create a real test image
        image = Image.new('RGB', (100, 100), color='red')
        image_io = BytesIO()
        image.save(image_io, format='JPEG')
        image_io.seek(0)
        
        test_image = SimpleUploadedFile(
            name="test_image.jpg",
            content=image_io.getvalue(),
            content_type="image/jpeg"
        )

        response = self.client.post(
            reverse("contact"),
            {"message": "Test message with image", "image": test_image},
        )

        self.assertEqual(response.status_code, 302)
        # Check if message was created
        messages = ContactMessage.objects.filter(message="Test message with image")
        self.assertTrue(messages.exists())
        message = messages.first()
        self.assertIsNotNone(message.image)
        self.assertEqual(message.sender, user)

    def test_contact_message_with_video_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = CustomUser.objects.create_user(
            username="videouser",
            email="videouser@example.com",
            password="StrongPass123!",
            name="Video User",
        )

        self.client.force_login(user)

        # Create a test video file (a simple binary file will suffice for video validation)
        test_video = SimpleUploadedFile(
            name="test_video.mp4",
            content=b"\x00\x00\x00\x18ftypmp42",  # MP4 file header
            content_type="video/mp4"
        )

        response = self.client.post(
            reverse("contact"),
            {"message": "Test message with video", "video": test_video},
        )

        self.assertEqual(response.status_code, 302)
        # Check if message was created
        messages = ContactMessage.objects.filter(message="Test message with video")
        self.assertTrue(messages.exists())

    def test_user_session_is_revoked_when_logging_in_elsewhere(self):
        user = CustomUser.objects.create_user(
            username="multiuser",
            email="multiuser@example.com",
            password="StrongPass123!",
            name="Multi User",
        )

        first_client = Client()
        first_client.post(
            reverse("login"),
            {"username": "multiuser@example.com", "password": "StrongPass123!"},
            follow=True,
        )
        self.assertTrue(first_client.session.get("_auth_user_id") == str(user.pk))

        second_client = Client()
        second_client.post(
            reverse("login"),
            {"username": "multiuser@example.com", "password": "StrongPass123!"},
            follow=True,
        )
        self.assertTrue(second_client.session.get("_auth_user_id") == str(user.pk))

        first_client.get(reverse("index"))
        self.assertNotIn("_auth_user_id", first_client.session)

    def test_course_video_is_served_inline_not_as_download(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = CustomUser.objects.create_user(
            username="videocourseuser",
            email="videocourseuser@example.com",
            password="StrongPass123!",
            name="Video Course User",
        )
        course = Course.objects.create(title="AI Bootcamp", price=99)
        module = Module.objects.create(
            course=course,
            title="Intro",
            video=SimpleUploadedFile(
                name="course-video.mp4",
                content=b"\x00\x00\x00\x18ftypmp42",
                content_type="video/mp4",
            ),
        )

        self.client.force_login(user)
        response = self.client.get(reverse("module_video", args=[module.id]))

        self.assertEqual(response.status_code, 200)
        content_disposition = response.get("Content-Disposition", "")
        self.assertIn("inline", content_disposition.lower())
        self.assertNotIn("attachment", content_disposition.lower())


class AuthApiTests(TestCase):
    def json_request(self, method, url, data=None, **kwargs):
        return getattr(self.client, method)(
            url,
            data=json.dumps(data or {}),
            content_type="application/json",
            **kwargs,
        )

    def test_register_and_login_issue_tokens(self):
        response = self.json_request("post", reverse("api_register"), {
            "name": "API User", "email": "api@example.com", "password": "StrongPass123!",
        })
        self.assertEqual(response.status_code, 201)
        self.assertIn("accessToken", response.json()["data"])
        self.assertIn("refreshToken", response.cookies)

        response = self.json_request("post", reverse("api_login"), {
            "email": "api@example.com", "password": "StrongPass123!",
        }, REMOTE_ADDR="192.0.2.10")
        self.assertEqual(response.status_code, 200)
        self.assertIn("accessToken", response.json()["data"])

    def test_api_register_rejects_malformed_email(self):
        response = self.json_request("post", reverse("api_register"), {
            "name": "Invalid Email", "email": "not-an-email", "password": "StrongPass123!",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "Enter a valid email address.")
        self.assertFalse(CustomUser.objects.filter(email="not-an-email").exists())

    def test_api_register_rejects_malformed_phone_number(self):
        response = self.json_request("post", reverse("api_register"), {
            "name": "Invalid Phone", "email": "api-phone@example.com",
            "phone_number": "+91 abc123", "password": "StrongPass123!",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "Enter a valid phone number.")
        self.assertFalse(CustomUser.objects.filter(email="api-phone@example.com").exists())

    def test_refresh_rotates_session_and_logout_all_revokes_sessions(self):
        user = CustomUser.objects.create_user(
            username="apiuser", email="apiuser@example.com", password="StrongPass123!",
        )
        login_response = self.json_request("post", reverse("api_login"), {
            "email": user.email, "password": "StrongPass123!",
        }, REMOTE_ADDR="192.0.2.11")
        access_token = login_response.json()["data"]["accessToken"]
        self.client.cookies["refreshToken"] = login_response.cookies["refreshToken"].value
        refresh_response = self.json_request("post", reverse("api_refresh"))
        self.assertEqual(refresh_response.status_code, 200)
        self.assertEqual(user.auth_sessions.filter(revoked_at__isnull=False).count(), 1)
        access_token = refresh_response.json()["data"]["accessToken"]

        response = self.json_request("post", reverse("api_logout_all"), HTTP_AUTHORIZATION=f"Bearer {access_token}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(user.auth_sessions.filter(revoked_at__isnull=True).exists())

    def test_new_api_login_invalidates_previous_access_token(self):
        user = CustomUser.objects.create_user(
            username="deviceapi", email="deviceapi@example.com", password="StrongPass123!",
        )
        first = self.json_request("post", reverse("api_login"), {
            "email": user.email, "password": "StrongPass123!",
        }, REMOTE_ADDR="192.0.2.13")
        first_token = first.json()["data"]["accessToken"]
        second = self.json_request("post", reverse("api_login"), {
            "email": user.email, "password": "StrongPass123!",
        }, REMOTE_ADDR="192.0.2.14")
        self.assertEqual(second.status_code, 200)

        response = self.client.get(
            reverse("api_me"),
            HTTP_AUTHORIZATION=f"Bearer {first_token}",
        )
        self.assertEqual(response.status_code, 401)

    def test_admin_dashboard_requires_staff_user(self):
        user = CustomUser.objects.create_user(
            username="regularapi", email="regularapi@example.com", password="StrongPass123!",
        )
        login_response = self.json_request("post", reverse("api_login"), {
            "email": user.email, "password": "StrongPass123!",
        }, REMOTE_ADDR="192.0.2.12")
        response = self.client.get(
            reverse("api_admin_dashboard"),
            HTTP_AUTHORIZATION=f"Bearer {login_response.json()['data']['accessToken']}",
        )
        self.assertEqual(response.status_code, 403)


class CheckoutCurrencyTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="gbpbuyer",
            email="gbpbuyer@example.com",
            password="StrongPass123!",
            name="GBP Buyer",
        )
        self.client.force_login(self.user)

    def test_checkout_page_renders_currency_selector_and_countries(self):
        response = self.client.get(reverse("checkout"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "COUNTRY / CURRENCY")
        self.assertContains(response, "United Kingdom")
        self.assertContains(response, "GBP")
        self.assertContains(response, "£")
        self.assertContains(response, "summary-header")

    @override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="rzp_test_secret")
    @patch("razorpay.Client")
    def test_create_order_with_gbp_currency(self, mock_razorpay_client):
        mock_instance = mock_razorpay_client.return_value
        mock_instance.order.create.return_value = {"id": "order_gbp_999"}

        response = self.client.post(
            reverse("create_order"),
            {
                "currency": "GBP",
                "amount": "14.87",
                "country_code": "GB",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["currency"], "GBP")
        self.assertEqual(data["amount"], 1487)
        self.assertEqual(data["order_id"], "order_gbp_999")

        order = Order.objects.get(razorpay_order_id="order_gbp_999")
        self.assertEqual(order.currency, "GBP")
        self.assertEqual(float(order.amount), 14.87)
        self.assertEqual(order.user, self.user)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="rzp_test_secret")
    @patch("razorpay.Client")
    def test_create_order_with_zero_decimal_currency(self, mock_razorpay_client):
        mock_instance = mock_razorpay_client.return_value
        mock_instance.order.create.return_value = {"id": "order_jpy_111"}

        response = self.client.post(
            reverse("create_order"),
            {
                "currency": "JPY",
                "amount": "2880",
                "country_code": "JP",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["currency"], "JPY")
        self.assertEqual(data["amount"], 2880)
        self.assertEqual(data["order_id"], "order_jpy_111")

    @override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="rzp_test_secret")
    @patch("razorpay.Client")
    def test_create_order_unsupported_currency_falls_back_to_inr(self, mock_razorpay_client):
        mock_instance = mock_razorpay_client.return_value

        # First call (AFN) raises exception, second call (INR fallback) succeeds
        mock_instance.order.create.side_effect = [
            Exception("Currency is not supported"),
            {"id": "order_inr_fallback_123"}
        ]

        response = self.client.post(
            reverse("create_order"),
            {
                "currency": "AFN",
                "amount": "1221.40",
                "inr_amount": "1791.69",
                "country_code": "AF",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["currency"], "INR")
        self.assertEqual(data["display_currency"], "AFN")
        self.assertEqual(data["amount"], 179169)
        self.assertEqual(data["order_id"], "order_inr_fallback_123")

        order = Order.objects.get(razorpay_order_id="order_inr_fallback_123")
        self.assertEqual(order.currency, "INR")
        self.assertEqual(float(order.amount), 1791.69)


class SeoOptimizationTests(TestCase):
    def test_robots_txt(self):
        response = self.client.get(reverse("robots_txt"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        self.assertContains(response, "User-agent: *")
        self.assertContains(response, "Allow: /")
        self.assertContains(response, "Disallow: /admin/")
        self.assertContains(response, "Sitemap: https://clout.courses/sitemap.xml")

    def test_sitemap_xml(self):
        response = self.client.get(reverse("sitemap_xml"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/xml", response["Content-Type"])
        self.assertContains(response, "<urlset")
        self.assertContains(response, "https://clout.courses/")
        self.assertContains(response, "https://clout.courses/course/")
        self.assertContains(response, "https://clout.courses/contact/")
        self.assertContains(response, "https://clout.courses/login/")
        self.assertContains(response, "https://clout.courses/register/")

    def test_homepage_seo_metadata(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>CLOUT — The AI Income Playbook | Make Money With AI</title>")
        self.assertContains(response, '<link rel="canonical" href="https://clout.courses/">')
        self.assertContains(response, '<meta name="description"')
        self.assertContains(response, '<meta property="og:title"')
        self.assertContains(response, '<meta name="twitter:card"')
        self.assertContains(response, '"@type": "Course"')
        self.assertContains(response, '"@type": "FAQPage"')

    def test_course_detail_seo_metadata(self):
        response = self.client.get(reverse("course_detail"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>Enroll in The AI Income Playbook — CLOUT</title>")
        self.assertContains(response, '<link rel="canonical" href="https://clout.courses/course/">')
        self.assertContains(response, '"@type": "Course"')

    def test_contact_seo_metadata(self):
        response = self.client.get(reverse("contact"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>Support & Contact — CLOUT</title>")
        self.assertContains(response, '<link rel="canonical" href="https://clout.courses/contact/">')
        self.assertContains(response, '"@type": "ContactPage"')

    def test_private_pages_have_noindex(self):
        response = self.client.get(reverse("forgot_password"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">')

    def test_favicon_endpoints(self):
        # Root favicon.ico
        response_ico = self.client.get("/favicon.ico")
        self.assertEqual(response_ico.status_code, 200)
        self.assertIn("image/x-icon", response_ico["Content-Type"])

        # Root favicon.png
        response_png = self.client.get("/favicon.png")
        self.assertEqual(response_png.status_code, 200)
        self.assertIn("image/png", response_png["Content-Type"])

        # Root apple-touch-icon.png
        response_apple = self.client.get("/apple-touch-icon.png")
        self.assertEqual(response_apple.status_code, 200)
        self.assertIn("image/png", response_apple["Content-Type"])

        # Root site.webmanifest
        response_manifest = self.client.get("/site.webmanifest")
        self.assertEqual(response_manifest.status_code, 200)

        # Root manifest.json
        response_json = self.client.get("/manifest.json")
        self.assertEqual(response_json.status_code, 200)

    def test_homepage_favicon_tags(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<link rel="icon" type="image/png" href="/favicon.png">')
        self.assertContains(response, '<link rel="apple-touch-icon" href="/favicon.png">')
        self.assertContains(response, '<link rel="icon" type="image/png" sizes="512x512" href="/static/favicon-512x512.png">')
        self.assertContains(response, '<link rel="icon" type="image/png" sizes="192x192" href="/static/favicon-192x192.png">')
        self.assertContains(response, '<link rel="icon" type="image/png" sizes="48x48" href="/static/favicon-48x48.png">')
        self.assertContains(response, '<link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">')
        self.assertContains(response, '<link rel="manifest" href="/static/site.webmanifest">')


class SubtitleSystemTests(TestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            username="admin_test",
            email="admin@example.com",
            password="AdminPass123!",
        )
        self.student = CustomUser.objects.create_user(
            username="student_test",
            email="student@example.com",
            password="StudentPass123!",
        )
        Order.objects.create(
            user=self.student,
            razorpay_order_id="order_sub_test_123",
            amount=499,
            paid=True,
        )
        self.course = Course.objects.create(
            title="AI Video Course",
            description="Testing subtitles",
            is_active=True,
        )
        self.module = Module.objects.create(
            course=self.course,
            title="Module 1",
            order=1,
        )
        self.lesson = Lesson.objects.create(
            module=self.module,
            title="Lesson 1",
            video_url="https://example.com/test_video.mp4",
            order=1,
            subtitle_status="ready",
        )

    def test_timestamp_utilities(self):
        from shop.subtitles import format_vtt_timestamp, format_srt_timestamp, parse_timestamp_to_seconds

        self.assertEqual(format_vtt_timestamp(0), "00:00:00.000")
        self.assertEqual(format_vtt_timestamp(65.432), "00:01:05.432")
        self.assertEqual(format_srt_timestamp(65.432), "00:01:05,432")
        self.assertAlmostEqual(parse_timestamp_to_seconds("00:01:05.432"), 65.432, places=3)
        self.assertAlmostEqual(parse_timestamp_to_seconds("01:05,432"), 65.432, places=3)

    def test_vtt_and_srt_generation_and_parsing(self):
        from shop.subtitles import cues_to_vtt, cues_to_srt, vtt_to_cues

        sample_cues = [
            {"id": 1, "start": 0.0, "end": 2.5, "text": "Welcome to the course."},
            {"id": 2, "start": 2.5, "end": 5.0, "text": "Let's learn AI together."},
        ]

        vtt_output = cues_to_vtt(sample_cues)
        self.assertTrue(vtt_output.startswith("WEBVTT"))
        self.assertIn("00:00:00.000 --> 00:00:02.500", vtt_output)
        self.assertIn("Welcome to the course.", vtt_output)

        srt_output = cues_to_srt(sample_cues)
        self.assertIn("00:00:00,000 --> 00:00:02,500", srt_output)
        self.assertIn("Let's learn AI together.", srt_output)

        parsed_cues = vtt_to_cues(vtt_output)
        self.assertEqual(len(parsed_cues), 2)
        self.assertEqual(parsed_cues[0]["text"], "Welcome to the course.")
        self.assertAlmostEqual(parsed_cues[0]["start"], 0.0)
        self.assertAlmostEqual(parsed_cues[0]["end"], 2.5)

    def test_subtitle_track_creation_and_serving(self):
        from shop.models import SubtitleTrack
        from shop.subtitles import cues_to_vtt, cues_to_srt

        cues = [
            {"id": 1, "start": 0.0, "end": 3.0, "text": "Hello world in English"},
        ]
        sub = SubtitleTrack.objects.create(
            lesson=self.lesson,
            language_code="en",
            language_name="English",
            is_original=True,
            cues_data=cues,
            vtt_content=cues_to_vtt(cues),
            srt_content=cues_to_srt(cues),
            status="ready",
        )

        self.assertEqual(str(sub), "Lesson 1 [English]")
        self.assertTrue(sub.vtt_public_url.startswith("/subtitles/vtt/"))
        self.assertTrue(sub.srt_public_url.startswith("/subtitles/srt/"))

        # Test serving VTT
        vtt_res = self.client.get(reverse("serve_subtitle_vtt", args=[sub.id]))
        self.assertEqual(vtt_res.status_code, 200)
        self.assertIn("text/vtt", vtt_res["Content-Type"])
        self.assertEqual(vtt_res["Access-Control-Allow-Origin"], "*")
        self.assertIn("Hello world in English", vtt_res.content.decode("utf-8"))

        # Test serving SRT
        srt_res = self.client.get(reverse("serve_subtitle_srt", args=[sub.id]))
        self.assertEqual(srt_res.status_code, 200)
        self.assertIn("attachment", srt_res["Content-Disposition"])
        self.assertIn("Hello world in English", srt_res.content.decode("utf-8"))

    def test_api_lesson_subtitles_endpoint(self):
        from shop.models import SubtitleTrack

        SubtitleTrack.objects.create(
            lesson=self.lesson,
            language_code="en",
            language_name="English",
            status="ready",
            cues_data=[{"id": 1, "start": 0.0, "end": 2.0, "text": "Hello"}],
        )
        SubtitleTrack.objects.create(
            lesson=self.lesson,
            language_code="ml",
            language_name="Malayalam",
            status="ready",
            cues_data=[{"id": 1, "start": 0.0, "end": 2.0, "text": "നമസ്കാരം"}],
        )

        # Student with course access
        self.client.force_login(self.student)
        res = self.client.get(reverse("api_lesson_subtitles", args=[self.lesson.id]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["subtitles"]), 2)
        lang_codes = [s["language_code"] for s in data["subtitles"]]
        self.assertIn("en", lang_codes)
        self.assertIn("ml", lang_codes)

    def test_admin_subtitle_cue_editor_api(self):
        from shop.models import SubtitleTrack

        sub = SubtitleTrack.objects.create(
            lesson=self.lesson,
            language_code="hi",
            language_name="Hindi",
            status="ready",
            cues_data=[{"id": 1, "start": 0.0, "end": 2.0, "text": "नमस्ते"}],
        )

        # Anonymous cannot access
        self.client.logout()
        res_anon = self.client.get(reverse("api_admin_subtitle_details", args=[sub.id]))
        self.assertNotEqual(res_anon.status_code, 200)

        # Admin can access details
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("api_admin_subtitle_details", args=[sub.id]))
        self.assertEqual(res_admin.status_code, 200)
        data = res_admin.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["subtitle"]["language_name"], "Hindi")

        # Admin can update cues
        updated_cues = [
            {"id": 1, "start": 0.5, "end": 3.0, "text": "नमस्ते दोस्तों, स्वागत है।"},
            {"id": 2, "start": 3.2, "end": 6.0, "text": "आज हम AI सीखेंगे।"},
        ]
        update_res = self.client.post(
            reverse("api_admin_update_cues", args=[sub.id]),
            data=json.dumps({"cues": updated_cues}),
            content_type="application/json",
        )
        self.assertEqual(update_res.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(len(sub.cues_data), 2)
        self.assertEqual(sub.cues_data[0]["text"], "नमस्ते दोस्तों, स्वागत है।")
        self.assertIn("WEBVTT", sub.vtt_content)

    def test_admin_target_languages_config_api(self):
        self.client.force_login(self.admin)

        # GET active languages
        get_res = self.client.get(reverse("api_admin_languages_config"))
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.json()
        self.assertTrue(get_data["success"])
        self.assertTrue(len(get_data["all_languages"]) > 8)

        # POST new active languages
        post_res = self.client.post(
            reverse("api_admin_languages_config"),
            data=json.dumps({"languages": ["en", "ml", "hi", "ta", "ar", "ja"]}),
            content_type="application/json",
        )
        self.assertEqual(post_res.status_code, 200)
        post_data = post_res.json()
        self.assertTrue(post_data["success"])
        self.assertEqual(post_data["active_languages"], ["en", "ml", "hi", "ta", "ar", "ja"])

    def test_admin_delete_subtitle_api(self):
        from shop.models import SubtitleTrack

        sub = SubtitleTrack.objects.create(
            lesson=self.lesson,
            language_code="es",
            language_name="Spanish",
            status="ready",
        )
        self.client.force_login(self.admin)
        del_res = self.client.post(reverse("api_admin_delete_subtitle", args=[sub.id]))
        self.assertEqual(del_res.status_code, 200)
        self.assertFalse(SubtitleTrack.objects.filter(id=sub.id).exists())

    @patch("shop.subtitles.transcribe_video_audio")
    @patch("shop.subtitles.resolve_video_file_to_local")
    @patch("shop.subtitles.translate_cues_to_language")
    def test_subtitle_generation_pipeline(self, mock_translate, mock_resolve, mock_transcribe):
        from shop.subtitles import process_subtitles_for_lesson
        from shop.models import SubtitleTrack

        mock_resolve.return_value = ("c:/dummy/video.mp4", False)
        mock_transcribe.return_value = (
            [
                {"id": 1, "start": 0.0, "end": 2.0, "text": "Welcome to the class."},
                {"id": 2, "start": 2.1, "end": 4.5, "text": "In this lesson we build software."},
            ],
            "English",
        )
        mock_translate.return_value = [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "ക്ലാസ്സിലേക്ക് സ്വാഗതം."},
            {"id": 2, "start": 2.1, "end": 4.5, "text": "ഈ പാഠത്തിൽ ഞങ്ങൾ സോഫ്റ്റ്‌വെയർ നിർമ്മിക്കുന്നു."},
        ]

        success = process_subtitles_for_lesson(self.lesson.id, target_languages=["en", "ml"])
        self.assertTrue(success)

        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.subtitle_status, "ready")
        self.assertEqual(self.lesson.detected_language, "English")

        en_track = SubtitleTrack.objects.filter(lesson=self.lesson, language_code="en").first()
        self.assertIsNotNone(en_track)
        self.assertTrue(en_track.is_original)
        self.assertEqual(len(en_track.cues_data), 2)

        ml_track = SubtitleTrack.objects.filter(lesson=self.lesson, language_code="ml").first()
        self.assertIsNotNone(ml_track)
        self.assertFalse(ml_track.is_original)
        self.assertIn("ക്ലാസ്സിലേക്ക് സ്വാഗതം", ml_track.vtt_content)





