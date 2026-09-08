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
        password = (
            os.getenv("ADMIN_PASSWORD", "").strip()
            or getattr(settings, "ADMIN_PASSWORD", "").strip()
            or "Mubashir@66"
        )

        user = CustomUser.objects.filter(username__iexact=username).first()
        if user is None:
            user = CustomUser.objects.filter(email__iexact=email).first()

        created = user is None
        if created:
            user = CustomUser(username=username, email=email)

        user.username = username
        user.email = email
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()

        # Also ensure mubashir account is active/staff if present
        mubashir_user = CustomUser.objects.filter(username__iexact="mubashir").first()
        if mubashir_user:
            mubashir_user.is_active = True
            mubashir_user.is_staff = True
            mubashir_user.is_superuser = True
            if not mubashir_user.check_password("Mubashir@66"):
                mubashir_user.set_password("Mubashir@66")
            mubashir_user.save()

        action = "Created" if created else "Repaired"
        self.stdout.write(self.style.SUCCESS(f"{action} admin account: {username} ({email})"))
