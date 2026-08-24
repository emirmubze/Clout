import os

from django.core.management.base import BaseCommand, CommandError

from shop.models import CustomUser


class Command(BaseCommand):
    help = "Create or repair the configured production administrator."

    def handle(self, *args, **options):
        username = os.getenv("ADMIN_USERNAME", "").strip()
        email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        password = os.getenv("ADMIN_PASSWORD", "")

        if not username or not email or not password:
            self.stdout.write(
                "Skipping admin provisioning: ADMIN_USERNAME, ADMIN_EMAIL, "
                "and ADMIN_PASSWORD are required."
            )
            return

        user, created = CustomUser.objects.get_or_create(
            username=username,
            defaults={"email": email},
        )

        user.email = email
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save(
            update_fields=[
                "email",
                "is_active",
                "is_staff",
                "is_superuser",
                "password",
            ]
        )

        action = "Created" if created else "Repaired"
        self.stdout.write(self.style.SUCCESS(f"{action} admin account: {username}"))
