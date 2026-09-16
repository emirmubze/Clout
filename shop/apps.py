from django.apps import AppConfig
from django.db.models.signals import post_migrate


def auto_ensure_admin(sender, **kwargs):
    try:
        from django.core.management import call_command
        call_command("ensure_admin")
    except Exception:
        pass


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shop"

    def ready(self):
        # Connect ensure_admin to post_migrate signal to avoid accessing the DB during app initialization
        post_migrate.connect(auto_ensure_admin, sender=self)

