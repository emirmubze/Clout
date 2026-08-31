from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Delete all non-admin users (keep superusers and staff)'

    def handle(self, *args, **options):
        # Get all non-admin users (not superuser and not staff)
        non_admin_users = User.objects.filter(is_superuser=False, is_staff=False)
        count = non_admin_users.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('No non-admin users to delete.'))
            return
        
        # Get list of usernames before deletion
        usernames = list(non_admin_users.values_list('username', flat=True))
        
        # Delete the users
        non_admin_users.delete()
        
        self.stdout.write(self.style.SUCCESS(f'Successfully deleted {count} non-admin users:'))
        for username in usernames:
            self.stdout.write(f'  - {username}')
