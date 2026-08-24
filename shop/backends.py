from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)

        if username is None or password is None:
            return None

        username = username.strip()

        user_model = get_user_model()

        try:
            user = user_model._default_manager.get(
                Q(username__iexact=username) | Q(email__iexact=username)
            )
        except user_model.DoesNotExist:
            return None
        except user_model.MultipleObjectsReturned:
            user = user_model._default_manager.filter(
                Q(username__iexact=username) | Q(email__iexact=username)
            ).order_by("id").first()

        if user and self.user_can_authenticate(user) and user.check_password(password):
            return user

        return None
