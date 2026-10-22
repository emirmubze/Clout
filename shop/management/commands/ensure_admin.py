import os

from django.conf import settings
from django.core.management.base import BaseCommand

from shop.models import CustomUser


class Command(BaseCommand):
    help = "Create or repair the configured production administrator."

    def handle(self, *args, **options):
        username = (
            os.getenv("ADMIN_USERNAME", "").strip()
            or getattr(settings, "ADMIN_USERNAME", "").strip()
            or "mubze"
        )
        email = (
            os.getenv("ADMIN_EMAIL", "").strip().lower()
            or getattr(settings, "ADMIN_EMAIL", "").strip().lower()
            or "emirmubze@gmail.com"
        )
        # Never fall back to a hard-coded password: this repository is public
        password = (
            os.getenv("ADMIN_PASSWORD", "").strip()
            or getattr(settings, "ADMIN_PASSWORD", "").strip()
        )

        user = CustomUser.objects.filter(username__iexact=username).first()
        if user is None:
            user = CustomUser.objects.filter(email__iexact=email).first()

        created = user is None
        if created:
            user = CustomUser(username=username, email=email)

        if password and (created or not user.has_usable_password()):
            user.set_password(password)
        elif created:
            user.set_unusable_password()

        if not user.has_usable_password():
            self.stdout.write(self.style.WARNING(
                f"Admin account {username} has no usable password. Set ADMIN_PASSWORD and run ensure_admin again."
            ))

        user.username = username
        user.email = email
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.save()

        # Also ensure mubashir account is active/staff if present without resetting password
        mubashir_user = CustomUser.objects.filter(username__iexact="mubashir").first()
        if mubashir_user:
            mubashir_user.is_active = True
            mubashir_user.is_staff = True
            mubashir_user.is_superuser = True
            if password and not mubashir_user.has_usable_password():
                mubashir_user.set_password(password)
            mubashir_user.save()

        action = "Created" if created else "Verified"
        self.stdout.write(self.style.SUCCESS(f"{action} admin account: {username} ({email})"))
