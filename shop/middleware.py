from django.contrib.auth import logout
from django.shortcuts import redirect

from .models import CustomUser


class SingleDeviceSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            current_session_key = request.session.session_key
            active_session_key = CustomUser.objects.values_list(
                "active_session_key",
                flat=True,
            ).get(pk=request.user.pk)

            if active_session_key and active_session_key != current_session_key:
                request.session.flush()
                logout(request)
                return redirect("login")

        return self.get_response(request)
