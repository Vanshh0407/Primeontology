from django.conf import settings
from django.http import HttpResponse


class SimpleCorsMiddleware:
    """Dev convenience so the Vite/Next host can call the API. Hosts with
    django-cors-headers should drop this middleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = HttpResponse(status=204) if request.method == "OPTIONS" else self.get_response(request)
        origin = request.headers.get("Origin", "")
        if origin in getattr(settings, "PRIME_ONTOLOGY_CORS_ORIGINS", []):
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response
