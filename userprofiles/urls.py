from django.urls import path
from .views import RegisterView, VerifyEmailView, PasswordResetRequestView, PasswordResetConfirmView
from rest_framework_simplejwt.views import (        
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('verify-email/confirm/', VerifyEmailView.as_view(), name='auth_verify_email'),
    path('login/', TokenObtainPairView.as_view(), name='auth_login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('password/reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password/reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]