from django.db import connections
from django.http import HttpResponse, JsonResponse

from .site_paths import get_admin_path


def healthz(request):
    """Report whether the web process and its primary database are ready."""
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def robots_txt(request):
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    admin_path = get_admin_path()
    disallowed_paths = [
        "/api/",
        "/accounts/",
        f"/{admin_path}",
        "/summernote/",
    ]
    content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            *[f"Disallow: {path}" for path in disallowed_paths],
            f"Sitemap: {sitemap_url}",
            "",
        ]
    )
    return HttpResponse(content, content_type="text/plain; charset=utf-8")
