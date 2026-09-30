"""Session login for the standalone app. Embedded hosts use their own authentication instead."""
import json

from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .identity import csrf_guard, get_identity

MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


def _who(request):
    ident = get_identity(request)
    return {"username": ident["user"], "role": ident["role"], "tenant": ident["tenant"]}


def _key(request, username):
    ip = request.META.get("REMOTE_ADDR", "?")
    return f"prime-login-fail:{ip}:{username.lower()}"


@csrf_exempt
@require_http_methods(["POST"])
def auth_login(request):
    blocked = csrf_guard(request)
    if blocked:
        return blocked
    try:
        b = json.loads(request.body or b"{}")
        username, password = str(b.get("username", "")).strip(), str(b.get("password", ""))
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not username or not password:
        return JsonResponse({"error": "Enter your username and password."}, status=400)
    key = _key(request, username)
    if cache.get(key, 0) >= MAX_FAILURES:
        return JsonResponse({"error": "Too many failed attempts. Try again in a few minutes."}, status=429)
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        cache.set(key, cache.get(key, 0) + 1, LOCKOUT_SECONDS)
        return JsonResponse({"error": "Invalid username or password."}, status=401)
    cache.delete(key)
    login(request, user)
    return JsonResponse(_who(request))


@csrf_exempt
@require_http_methods(["POST"])
def auth_logout(request):
    blocked = csrf_guard(request)
    if blocked:
        return blocked
    logout(request)
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["GET"])
def auth_me(request):
    who = _who(request)
    if who["role"] == "none":
        return JsonResponse({"authenticated": False}, status=401)
    return JsonResponse({"authenticated": True, **who})
