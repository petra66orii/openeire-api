from django.http import HttpResponse

from .site_paths import get_admin_path


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
