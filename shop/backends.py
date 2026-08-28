from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
import re


class EmailOrUsernameModelBackend(ModelBackend):
    """
    Custom authentication backend that allows signing in using:
    - Original registered Email address
    - Original registered Phone number (in local or international format)
    - Username
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = (
                kwargs.get(get_user_model().USERNAME_FIELD)
                or kwargs.get("email")
                or kwargs.get("phone_number")
                or kwargs.get("phone")
            )

        if username is None or password is None:
            return None

        identifier = str(username).strip()
        if not identifier:
            return None

        user_model = get_user_model()

        # Build search query for email or username
        query = Q(username__iexact=identifier) | Q(email__iexact=identifier)

        # Phone matching: match exact string, or match by normalized digits
        digits_only = re.sub(r"[^\d]", "", identifier)
        if digits_only:
            query |= Q(phone_number__iexact=identifier)
            if len(digits_only) >= 10:
                query |= Q(phone_number__endswith=digits_only[-10:])
                query |= Q(phone_number__icontains=digits_only)
        else:
            query |= Q(phone_number__iexact=identifier)

        try:
            user = user_model._default_manager.get(query)
        except user_model.DoesNotExist:
            return None
        except user_model.MultipleObjectsReturned:
            # Prioritize exact email, exact phone, or exact username match
            user = (
                user_model._default_manager.filter(email__iexact=identifier).first()
                or user_model._default_manager.filter(phone_number__iexact=identifier).first()
                or user_model._default_manager.filter(username__iexact=identifier).first()
                or user_model._default_manager.filter(query).order_by("id").first()
            )

        if user and self.user_can_authenticate(user) and user.check_password(password):
            return user

        return None

