import json
import os
from unittest.mock import patch
from smtplib import SMTPException

from django.core import mail
from django.core.management import call_command
from django.middleware.csrf import get_token
from django.test import Client, TestCase, override_settings
from django.urls import reverse

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
