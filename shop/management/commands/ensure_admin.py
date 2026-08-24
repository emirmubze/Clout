import os

from django.core.management.base import BaseCommand

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

        user = CustomUser.objects.filter(username=username).first()
        if user is None:
            user = CustomUser.objects.filter(email__iexact=email).first()

        created = user is None
        if created:
            user = CustomUser(username=username, email=email)

        user.email = email
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        if created:
            user.save()
        else:
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
