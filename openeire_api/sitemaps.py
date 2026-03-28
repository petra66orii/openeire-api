from urllib.parse import urljoin

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.core.exceptions import ImproperlyConfigured

from blog.models import BlogPost
from products.models import Photo


def _frontend_base_url():
    value = getattr(settings, "FRONTEND_URL", None)
    if not value:
        raise ImproperlyConfigured(
            "FRONTEND_URL must be set to build frontend sitemap URLs."
        )
    return f"{str(value).rstrip('/')}/"


def _frontend_url(path):
    return urljoin(_frontend_base_url(), path.lstrip("/"))


class FrontendAbsoluteUrlSitemap(Sitemap):
    protocol = "https"

    def get_urls(self, page=1, site=None, protocol=None):
        urls = []
        paginator_page = self.paginator.page(page)
        for item in paginator_page.object_list:
            urls.append(
                {
                    "item": item,
                    "location": self.location(item),
                    "lastmod": self.lastmod(item) if hasattr(self, "lastmod") else None,
                    "changefreq": getattr(self, "changefreq", None),
                    "priority": getattr(self, "priority", None),
                    "alternates": [],
                }
            )
        return urls


class StaticPageSitemap(FrontendAbsoluteUrlSitemap):
    changefreq = "weekly"
    priority = 0.7

    pages = (
        {"path": "/", "priority": 1.0},
        {"path": "/gallery", "priority": 0.8},
        {"path": "/gallery/physical", "priority": 0.8},
        {"path": "/blog", "priority": 0.8},
        {"path": "/about", "priority": 0.6},
        {"path": "/contact", "priority": 0.6},
        {"path": "/licensing", "priority": 0.7},
        {"path": "/terms", "priority": 0.4},
        {"path": "/shipping", "priority": 0.4},
        {"path": "/refunds", "priority": 0.4},
        {"path": "/privacy", "priority": 0.4},
    )

    def items(self):
        return self.pages

    def location(self, item):
        return _frontend_url(item["path"])

    def priority(self, item):
        return item["priority"]


class BlogPostSitemap(FrontendAbsoluteUrlSitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return BlogPost.objects.filter(status=1).order_by("-updated_at")

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return _frontend_url(f"/blog/{obj.slug}")


class PhysicalPhotoSitemap(FrontendAbsoluteUrlSitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return (
            Photo.objects.filter(
                is_active=True,
                is_printable=True,
                variants__isnull=False,
            )
            .distinct()
            .order_by("-created_at")
        )

    def lastmod(self, obj):
        return obj.created_at

    def location(self, obj):
        return _frontend_url(f"/gallery/physical/{obj.id}")


sitemaps = {
    "static": StaticPageSitemap,
    "blog": BlogPostSitemap,
    "physical": PhysicalPhotoSitemap,
}
