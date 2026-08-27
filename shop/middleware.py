from django.contrib.auth import logout
from django.shortcuts import redirect

from .models import CustomUser


class SingleDeviceSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            current_session_key = request.session.session_key
            active_session_key = getattr(request.user, "active_session_key", "")

            if current_session_key and active_session_key != current_session_key:
                request.user.active_session_key = current_session_key
                request.user.save(update_fields=["active_session_key"])

        return self.get_response(request)
