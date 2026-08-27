from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
	help = "Create or update the configured production administrator."

	def handle(self, *args, **options):
		username = settings.ADMIN_USERNAME
		email = settings.ADMIN_EMAIL
		password = settings.ADMIN_PASSWORD

		if not username or not email or not password:
			self.stdout.write(
				"Skipping admin provisioning: ADMIN_USERNAME, ADMIN_EMAIL, "
				"and ADMIN_PASSWORD are required."
			)
			return

		user_model = get_user_model()
		user = user_model.objects.filter(username__iexact=username).first()
		if user is None:
			user = user_model.objects.filter(email__iexact=email).first()

		created = user is None
		if created:
			user = user_model(username=username, email=email)

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

		action = "Created" if created else "Updated"
		self.stdout.write(self.style.SUCCESS(f"Admin ready: {username} ({action})"))
