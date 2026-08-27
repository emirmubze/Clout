import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import timedelta
from functools import wraps

from django.contrib.auth import authenticate
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AuthSession, CustomUser


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret():
    return os.getenv("JWT_ACCESS_SECRET", os.getenv("DJANGO_SECRET_KEY", "change-me"))


def _token(payload):
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    message = f"{header}.{body}".encode()
    signature = _b64(hmac.new(_secret().encode(), message, hashlib.sha256).digest())
    return f"{header}.{body}.{signature}"


def _decode(token):
    header, body, signature = token.split(".")
    message = f"{header}.{body}".encode()
    expected = _b64(hmac.new(_secret().encode(), message, hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid token")
    payload = json.loads(_unb64(body))
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise ValueError("Token expired")
    return payload


def _json(request):
    try:
        data = json.loads(request.body or b"{}")
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        return None


def _user_data(user):
    return {"id": str(user.pk), "name": user.name, "email": user.email, "role": "admin" if user.is_staff else "user"}


def revoke_api_sessions(user, keep_session_key=None):
    AuthSession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())

    for session in Session.objects.all():
        try:
            session_data = session.get_decoded()
        except Exception:
            continue
        if (
            session_data.get("_auth_user_id") == str(user.pk)
            and session.session_key != keep_session_key
        ):
            session.delete()

    if keep_session_key:
        user.active_session_key = keep_session_key
        user.save(update_fields=["active_session_key"])
    elif user.active_session_key:
        user.active_session_key = ""
        user.save(update_fields=["active_session_key"])


def _tokens(user, revoke_existing=False):
    if revoke_existing:
        revoke_api_sessions(user)
    now = int(time.time())
    access_jti = secrets.token_urlsafe(18)
    refresh_jti = secrets.token_urlsafe(18)
    access_minutes = int(os.getenv("JWT_ACCESS_EXPIRES_MINUTES", "10"))
    refresh_days = int(os.getenv("JWT_REFRESH_EXPIRES_DAYS", "7"))
    AuthSession.objects.create(user=user, access_jti=access_jti, refresh_jti=refresh_jti, expires_at=timezone.now() + timedelta(days=refresh_days))
    access = _token({"sub": str(user.pk), "role": "admin" if user.is_staff else "user", "jti": access_jti, "type": "access", "exp": now + access_minutes * 60})
    refresh = _token({"sub": str(user.pk), "jti": refresh_jti, "type": "refresh", "exp": now + refresh_days * 86400})
    return access, refresh


def _set_refresh(response, token):
    response.set_cookie("refreshToken", token, httponly=True, secure=not os.getenv("DEBUG", "True").lower() == "true", samesite="Strict", max_age=int(os.getenv("JWT_REFRESH_EXPIRES_DAYS", "7")) * 86400)


def _auth_user(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        payload = _decode(header[7:])
        if payload.get("type") != "access":
            return None
        session = AuthSession.objects.get(
            user_id=payload["sub"],
            access_jti=payload["jti"],
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        return session.user
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, AuthSession.DoesNotExist):
        return None


def require_auth(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = _auth_user(request)
        if not user:
            return JsonResponse({"success": False, "message": "Authentication required."}, status=401)
        request.api_user = user
        return view(request, *args, **kwargs)
    return wrapped


def _rate_limited(request):
    key = f"auth-login:{request.META.get('REMOTE_ADDR', 'unknown')}"
    attempts = cache.get(key, 0)
    if attempts >= 5:
        return True
    cache.set(key, attempts + 1, 900)
    return False


@require_POST
def register(request):
    data = _json(request)
    if not data:
        return JsonResponse({"success": False, "message": "A JSON body is required."}, status=400)
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    if not name or not email or len(password) < 8:
        return JsonResponse({"success": False, "message": "Name, email, and a password of at least 8 characters are required."}, status=400)
    if CustomUser.objects.filter(email__iexact=email).exists():
        return JsonResponse({"success": False, "message": "An account with this email already exists."}, status=409)
    username = email.split("@", 1)[0][:140] or "user"
    base = username
    suffix = 1
    while CustomUser.objects.filter(username=username).exists():
        username = f"{base}{suffix}"
        suffix += 1
    user = CustomUser.objects.create_user(username=username, email=email, name=name, password=password)
    access, refresh = _tokens(user, revoke_existing=True)
    response = JsonResponse({"success": True, "message": "User created successfully", "data": {"user": _user_data(user), "accessToken": access}}, status=201)
    _set_refresh(response, refresh)
    return response


@require_POST
def login(request):
    if _rate_limited(request):
        return JsonResponse({"success": False, "message": "Too many login attempts. Try again later."}, status=429)
    data = _json(request)
    if not data:
        return JsonResponse({"success": False, "message": "A JSON body is required."}, status=400)
    email = str(data.get("email", "")).strip()
    user = authenticate(request, username=email, password=data.get("password"))
    if not user:
        return JsonResponse({"success": False, "message": "Invalid email or password."}, status=401)
    access, refresh = _tokens(user, revoke_existing=True)
    response = JsonResponse({"success": True, "message": "Logged in successfully", "data": {"user": _user_data(user), "accessToken": access}})
    _set_refresh(response, refresh)
    return response


@require_auth
def me(request):
    return JsonResponse({"success": True, "data": {"user": _user_data(request.api_user)}})


@require_auth
def change_password(request):
    data = _json(request)
    current = str(data.get("currentPassword", "")) if data else ""
    new = str(data.get("newPassword", "")) if data else ""
    if not request.api_user.check_password(current):
        return JsonResponse({"success": False, "message": "Current password is incorrect."}, status=400)
    if len(new) < 8:
        return JsonResponse({"success": False, "message": "New password must be at least 8 characters."}, status=400)
    request.api_user.set_password(new)
    request.api_user.save(update_fields=["password"])
    AuthSession.objects.filter(user=request.api_user).update(revoked_at=timezone.now())
    return JsonResponse({"success": True, "message": "Password changed successfully."})


@require_POST
def refresh(request):
    try:
        payload = _decode(request.COOKIES["refreshToken"])
        if payload.get("type") != "refresh":
            raise ValueError
        session = AuthSession.objects.get(refresh_jti=payload["jti"], user_id=payload["sub"], revoked_at__isnull=True, expires_at__gt=timezone.now())
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at"])
        access, new_refresh = _tokens(session.user)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, AuthSession.DoesNotExist):
        return JsonResponse({"success": False, "message": "Invalid or expired refresh token."}, status=401)
    response = JsonResponse({"success": True, "data": {"accessToken": access}})
    _set_refresh(response, new_refresh)
    return response


@require_POST
def logout(request):
    token = request.COOKIES.get("refreshToken")
    if token:
        try:
            AuthSession.objects.filter(refresh_jti=_decode(token)["jti"]).update(revoked_at=timezone.now())
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
    response = JsonResponse({"success": True, "message": "Logged out successfully."})
    response.delete_cookie("refreshToken")
    return response


@require_auth
def logout_all(request):
    AuthSession.objects.filter(user=request.api_user, revoked_at__isnull=True).update(revoked_at=timezone.now())
    return JsonResponse({"success": True, "message": "Logged out from all devices."})


@require_auth
def admin_dashboard(request):
    if not request.api_user.is_staff:
        return JsonResponse({"success": False, "message": "Admin access required."}, status=403)
    return JsonResponse({"success": True, "message": "Welcome Admin! You can see this dashboard.", "user": _user_data(request.api_user)})