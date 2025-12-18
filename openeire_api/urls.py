from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from .admin import custom_admin_site
from userprofiles.views import GoogleLogin

urlpatterns = [
    path('admin/', custom_admin_site.urls),
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