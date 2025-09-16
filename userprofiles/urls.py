from django.urls import path
from .views import RegisterView, VerifyEmailView
from rest_framework_simplejwt.views import (        
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('verify-email/confirm/', VerifyEmailView.as_view(), name='auth_verify_email'),
    path('login/', TokenObtainPairView.as_view(), name='auth_login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]