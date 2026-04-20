from django.http import HttpResponse


def robots_txt(request):
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    disallowed_paths = [
        "/api/",
        "/accounts/",
        "/admin/",
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
