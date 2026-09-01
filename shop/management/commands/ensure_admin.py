import os

from django.core.management.base import BaseCommand

from shop.models import CustomUser


class Command(BaseCommand):
    help = "Create or repair the configured production administrator."

    def handle(self, *args, **options):
        username = os.getenv("ADMIN_USERNAME", "").strip() or "mubze"
        email = os.getenv("ADMIN_EMAIL", "").strip().lower() or "emirmubze@gmail.com"
        password = os.getenv("ADMIN_PASSWORD", "").strip() or "Admin@Clout2026!"

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
        password_changed = not user.check_password(password)
        if password_changed:
            user.set_password(password)
        if created:
            user.save()
        else:
            update_fields = [
                "username",
                "email",
                "is_active",
                "is_staff",
                "is_superuser",
            ]
            if password_changed:
                update_fields.append("password")
            user.save(update_fields=update_fields)

        action = "Created" if created else "Repaired"
        self.stdout.write(self.style.SUCCESS(f"{action} admin account: {username}"))
