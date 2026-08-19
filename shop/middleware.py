from django.contrib.auth import logout
from django.shortcuts import redirect


class SingleDeviceSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            current_session_key = request.session.session_key
            expected_session_key = request.session.get("single_device_session_key")

            if expected_session_key is None:
                request.session["single_device_session_key"] = current_session_key
                request.session.modified = True
                return self.get_response(request)

            if expected_session_key != current_session_key:
                request.session.flush()
                logout(request)
                return redirect("login")

        return self.get_response(request)
