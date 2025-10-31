from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterView,
    VerifyEmailView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    UserProfileView,
    ResendVerificationView,
    MyTokenObtainPairView,
    ChangePasswordView
    )

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('verify-email/confirm/', VerifyEmailView.as_view(), name='auth_verify_email'),
    path('resend-verification/', ResendVerificationView.as_view(), name='auth_resend_verification'),
    path('login/', MyTokenObtainPairView.as_view(), name='auth_login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('password/reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password/reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password/change/', ChangePasswordView.as_view(), name='auth_password_change'),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
]