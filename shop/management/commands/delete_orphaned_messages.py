from django.core.management.base import BaseCommand
from shop.models import ContactMessage

class Command(BaseCommand):
    help = 'Delete all orphaned contact messages (no sender or recipient)'

    def handle(self, *args, **options):
        # Delete messages with no sender and no recipient
        orphaned_messages = ContactMessage.objects.filter(sender__isnull=True, recipient__isnull=True)
        count = orphaned_messages.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('No orphaned messages to delete.'))
            return
        
        orphaned_messages.delete()
        self.stdout.write(self.style.SUCCESS(f'Successfully deleted {count} orphaned contact messages.'))
