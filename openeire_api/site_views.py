from django.http import HttpResponse


def robots_txt(request):
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    content = "\n".join(
        [
            "User-agent: *",
            "Disallow: /",
            f"Sitemap: {sitemap_url}",
            "",
        ]
    )
    return HttpResponse(content, content_type="text/plain; charset=utf-8")
