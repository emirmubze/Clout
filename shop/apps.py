from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shop"

    def ready(self):
        # Auto-provision/repair administrator account on app boot
        import sys
        if any(cmd in " ".join(sys.argv) for cmd in ("runserver", "gunicorn", "wsgi", "asgi", "start.sh", "build.sh")):
            try:
                from django.core.management import call_command
                call_command("ensure_admin")
            except Exception:
                pass
