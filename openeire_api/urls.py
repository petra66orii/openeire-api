from django.contrib import admin
from django.contrib.sitemaps.views import index, sitemap
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from .admin import custom_admin_site
from .site_paths import get_admin_path
from .site_views import robots_txt
from .sitemaps import sitemaps
from userprofiles.views import GoogleLogin

ADMIN_URL = get_admin_path()

urlpatterns = [
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', index, {'sitemaps': sitemaps, 'sitemap_url_name': 'sitemap_section'}, name='sitemap_index'),
    path('sitemap-<section>.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap_section'),
    path(ADMIN_URL, custom_admin_site.urls),
    path('accounts/', include('allauth.urls')),
    path('summernote/', include('django_summernote.urls')),
    path('api/auth/', include('userprofiles.urls')),
    path('api/', include('products.urls')),
    path('api/blog/', include('blog.urls')),
    path('api/home/', include('home.urls')),
    path('api/checkout/', include('checkout.urls')),
    path('api/auth/google/', GoogleLogin.as_view(), name='google_login'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
