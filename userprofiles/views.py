import logging
import secrets
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django_countries import countries
from .serializers import UserSerializer, PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView
from .models import UserProfile
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .serializers import (
    UserProfileSerializer,
    ResendVerificationSerializer,
    MyTokenObtainPairSerializer,
    ChangePasswordSerializer,
    ChangeEmailSerializer,
    DeleteAccountSerializer,
)
from .token_utils import (
    EMAIL_VERIFICATION_PURPOSE,
    PASSWORD_RESET_PURPOSE,
    decode_user_action_token,
    issue_user_action_token,
)
from checkout.order_claiming import claim_guest_orders_for_user
from openeire_api.mail_utils import get_default_from_email

logger = logging.getLogger(__name__)


def get_frontend_url():
    frontend_url = getattr(settings, "FRONTEND_URL", None)
    if frontend_url:
        return str(frontend_url).rstrip("/")
    if getattr(settings, "DEBUG", False) or getattr(settings, "IS_TEST_ENV", False):
        return "http://localhost:5173"
    raise ImproperlyConfigured("FRONTEND_URL must be configured when DEBUG is False.")


def _token_minutes(setting_name, default_value):
    raw = getattr(settings, setting_name, default_value)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed > 0 else default_value


def _set_jwt_cookie(response, *, name, value, max_age):
    domain = getattr(settings, "JWT_COOKIE_DOMAIN", None) or None
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        secure=bool(getattr(settings, "JWT_COOKIE_SECURE", True)),
        httponly=True,
        samesite=getattr(settings, "JWT_COOKIE_SAMESITE", "Lax"),
        path="/",
        domain=domain,
    )


def _set_jwt_cookies_if_enabled(response, *, access_token=None, refresh_token=None):
    if not bool(getattr(settings, "JWT_USE_HTTPONLY_COOKIES", False)):
        return

    csrf_max_age = None
    if access_token:
        access_lifetime = int(getattr(settings, "SIMPLE_JWT", {}).get("ACCESS_TOKEN_LIFETIME").total_seconds())
        csrf_max_age = access_lifetime
        _set_jwt_cookie(
            response,
            name=getattr(settings, "JWT_ACCESS_COOKIE_NAME", "openeire_access"),
            value=access_token,
            max_age=access_lifetime,
        )
    if refresh_token:
        refresh_lifetime = int(getattr(settings, "SIMPLE_JWT", {}).get("REFRESH_TOKEN_LIFETIME").total_seconds())
        csrf_max_age = refresh_lifetime
        _set_jwt_cookie(
            response,
            name=getattr(settings, "JWT_REFRESH_COOKIE_NAME", "openeire_refresh"),
            value=refresh_token,
            max_age=refresh_lifetime,
        )
    if bool(getattr(settings, "JWT_COOKIE_CSRF_PROTECTION", True)):
        csrf_cookie_name = getattr(settings, "JWT_CSRF_COOKIE_NAME", "openeire_csrf")
        csrf_token = secrets.token_urlsafe(32)
        response.set_cookie(
            key=csrf_cookie_name,
            value=csrf_token,
            max_age=csrf_max_age,
            secure=bool(getattr(settings, "JWT_COOKIE_SECURE", True)),
            httponly=False,
            samesite=getattr(settings, "JWT_COOKIE_SAMESITE", "Lax"),
            path="/",
            domain=getattr(settings, "JWT_COOKIE_DOMAIN", None) or None,
        )


def _clear_jwt_cookies(response):
    domain = getattr(settings, "JWT_COOKIE_DOMAIN", None) or None
    samesite = getattr(settings, "JWT_COOKIE_SAMESITE", "Lax")
    response.delete_cookie(
        getattr(settings, "JWT_ACCESS_COOKIE_NAME", "openeire_access"),
        path="/",
        domain=domain,
        samesite=samesite,
    )
    response.delete_cookie(
        getattr(settings, "JWT_REFRESH_COOKIE_NAME", "openeire_refresh"),
        path="/",
        domain=domain,
        samesite=samesite,
    )
    response.delete_cookie(
        getattr(settings, "JWT_CSRF_COOKIE_NAME", "openeire_csrf"),
        path="/",
        domain=domain,
        samesite=samesite,
    )


def _is_cookie_mode_enabled():
    return bool(getattr(settings, "JWT_USE_HTTPONLY_COOKIES", False))


def _enforce_cookie_csrf(request):
    if not _is_cookie_mode_enabled():
        return
    if not bool(getattr(settings, "JWT_COOKIE_CSRF_PROTECTION", True)):
        return

    csrf_cookie_name = getattr(settings, "JWT_CSRF_COOKIE_NAME", "openeire_csrf")
    csrf_header_name = getattr(settings, "JWT_CSRF_HEADER_NAME", "HTTP_X_CSRFTOKEN")
    csrf_cookie = request.COOKIES.get(csrf_cookie_name)
    csrf_header = request.META.get(csrf_header_name)
    if not csrf_cookie or not csrf_header:
        raise PermissionDenied("CSRF token missing.")
    if not secrets.compare_digest(str(csrf_cookie), str(csrf_header)):
        raise PermissionDenied("CSRF token mismatch.")


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = UserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # Generate a token for the user
        token = issue_user_action_token(
            user=user,
            purpose=EMAIL_VERIFICATION_PURPOSE,
            lifetime_minutes=_token_minutes("EMAIL_VERIFICATION_TOKEN_MINUTES", 60),
        )
        
        # --- Email Sending Logic ---
        frontend_url = get_frontend_url()
        verification_link = f"{frontend_url}/verify-email/confirm/{token}"
        
        subject = 'Welcome to OpenÉire Studios! Verify Your Email'
        context = {
            'username': user.username,
            'verification_link': verification_link
        }
        
        html_message = render_to_string('emails/verification_email.html', context)
        plain_message = strip_tags(html_message)
        from_email = get_default_from_email()
        to_email = user.email

        try:
            send_mail(subject, plain_message, from_email, [to_email], html_message=html_message)
            logger.info("Verification email sent for user_id=%s", user.id)
        except Exception:
            logger.exception("Failed to send verification email for user_id=%s", user.id)
        # --- End of Email Sending Logic ---

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    

class VerifyEmailView(APIView):
    """
    API endpoint to verify a user's email with a token.
    """
    permission_classes = (AllowAny,)

    def post(self, request, *args, **kwargs):
        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = decode_user_action_token(
                token=token,
                expected_purpose=EMAIL_VERIFICATION_PURPOSE,
            )
            user = User.objects.get(id=user_id)

            if user.is_active:
                return Response({'message': 'Account already verified.'}, status=status.HTTP_200_OK)

            user.is_active = True
            user.save()
            return Response({'message': 'Email successfully verified!'}, status=status.HTTP_200_OK)

        except (TokenError, ValueError):
            return Response({'error': 'Invalid activation link.'}, status=status.HTTP_400_BAD_REQUEST)
        except User.DoesNotExist:
            return Response({'error': 'Invalid activation link.'}, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetRequestView(generics.GenericAPIView):
    """
    API endpoint to request a password reset.
    """
    serializer_class = PasswordResetRequestSerializer
    permission_classes = [AllowAny]
    GENERIC_SUCCESS_MESSAGE = "Password reset link sent."

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        matches = list(User.objects.filter(email__iexact=email).only("id", "username", "email", "is_active")[:2])

        if len(matches) != 1:
            if len(matches) > 1:
                logger.warning("Password reset skipped due to duplicate email records.")
            return Response({"message": self.GENERIC_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)

        user = matches[0]

        # Generate a short-lived token for the user
        token = issue_user_action_token(
            user=user,
            purpose=PASSWORD_RESET_PURPOSE,
            lifetime_minutes=_token_minutes("PASSWORD_RESET_TOKEN_MINUTES", 30),
        )

        frontend_url = get_frontend_url()
        reset_link = f"{frontend_url}/password-reset/confirm/{token}"
        subject = "OpenÉire Studios - Reset Your Password"
        context = {
            "username": user.username,
            "reset_link": reset_link,
        }
        html_message = render_to_string("emails/password_email_reset.html", context)
        plain_message = strip_tags(html_message)

        try:
            send_mail(
                subject,
                plain_message,
                get_default_from_email(),
                [user.email],
                html_message=html_message,
            )
            logger.info("Password reset email sent for user_id=%s", user.id)
        except Exception:
            logger.exception("Failed to send password reset email for user_id=%s", user.id)
        return Response({"message": self.GENERIC_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)


class PasswordResetConfirmView(generics.GenericAPIView):
    """
    API endpoint to confirm a password reset.
    """
    serializer_class = PasswordResetConfirmSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        token = request.data.get('token')
        password = serializer.validated_data['password']

        try:
            user_id = decode_user_action_token(
                token=token,
                expected_purpose=PASSWORD_RESET_PURPOSE,
            )
            user = User.objects.get(id=user_id)
            user.set_password(password)
            user.save()
            return Response({"message": "Password reset successful."}, status=status.HTTP_200_OK)
        except (TokenError, ValueError, User.DoesNotExist):
            return Response({"error": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)
        
class UserProfileView(generics.RetrieveUpdateAPIView):
    """
    API endpoint for viewing and editing the user's profile.
    """
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        """
        This view should return an object for the currently authenticated user.
        """
        return self.request.user.userprofile
    
class ResendVerificationView(generics.GenericAPIView):
    """
    API endpoint to resend the verification email.
    """
    serializer_class = ResendVerificationSerializer
    permission_classes = [AllowAny]
    GENERIC_SUCCESS_MESSAGE = "Verification email sent."

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        matches = list(User.objects.filter(email__iexact=email).only("id", "username", "email", "is_active")[:2])

        if len(matches) != 1:
            if len(matches) > 1:
                logger.warning("Resend verification skipped due to duplicate email records.")
            return Response({"message": self.GENERIC_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)

        user = matches[0]
        if user.is_active:
            return Response({"message": self.GENERIC_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)

        # Generate a new token
        token = issue_user_action_token(
            user=user,
            purpose=EMAIL_VERIFICATION_PURPOSE,
            lifetime_minutes=_token_minutes("EMAIL_VERIFICATION_TOKEN_MINUTES", 60),
        )

        # Resend the email (reuse logic from RegisterView)
        frontend_url = get_frontend_url()
        verification_link = f"{frontend_url}/verify-email/confirm/{token}"
        subject = 'Verify Your OpenÉire Studios Email Address'
        context = {'username': user.username, 'verification_link': verification_link}
        html_message = render_to_string('emails/verification_email.html', context)
        plain_message = strip_tags(html_message)
        from_email = get_default_from_email()
        to_email = user.email

        try:
            send_mail(subject, plain_message, from_email, [to_email], html_message=html_message)
            logger.info("Verification email resent for user_id=%s", user.id)
        except Exception:
            logger.exception("Failed to resend verification email for user_id=%s", user.id)
        return Response({"message": self.GENERIC_SUCCESS_MESSAGE}, status=status.HTTP_200_OK)

class MyTokenObtainPairView(TokenObtainPairView):
    """
    Custom view using the custom serializer to allow email/username login.
    """
    serializer_class = MyTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = Response(serializer.validated_data, status=status.HTTP_200_OK)
        authenticated_user = getattr(serializer, "user", None)
        if authenticated_user is not None:
            try:
                claimed_count = claim_guest_orders_for_user(authenticated_user)
            except Exception:
                logger.exception(
                    "Guest order claiming failed during login. user_id=%s",
                    authenticated_user.id,
                )
            else:
                if claimed_count:
                    logger.info(
                        "Claimed %s guest orders for user_id=%s during login.",
                        claimed_count,
                        authenticated_user.id,
                    )

        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")
        _set_jwt_cookies_if_enabled(
            response,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        return response


class MyTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        payload = request.data.copy()
        if _is_cookie_mode_enabled() and not payload.get("refresh"):
            cookie_name = getattr(settings, "JWT_REFRESH_COOKIE_NAME", "openeire_refresh")
            cookie_refresh = request.COOKIES.get(cookie_name)
            if cookie_refresh:
                _enforce_cookie_csrf(request)
                payload["refresh"] = cookie_refresh

        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        response = Response(serializer.validated_data, status=status.HTTP_200_OK)
        access_token = response.data.get("access")
        refresh_token = response.data.get("refresh")
        _set_jwt_cookies_if_enabled(
            response,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        return response


class ChangePasswordView(generics.UpdateAPIView):
    """
    An endpoint for changing the password.
    """
    serializer_class = ChangePasswordSerializer
    model = User
    permission_classes = [IsAuthenticated]

    def get_object(self, queryset=None):
        return self.request.user

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid(raise_exception=True):
            # The serializer's .update() method handles password hashing and saving
            serializer.save()
            return Response({"message": "Password updated successfully"}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ChangeEmailView(generics.UpdateAPIView):
    """
    An endpoint for changing the user's email.
    Requires the current password.
    """
    serializer_class = ChangeEmailSerializer
    model = User
    permission_classes = [IsAuthenticated]

    def get_object(self, queryset=None):
        return self.request.user

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        serializer = self.get_serializer(instance=self.object, data=request.data)

        if serializer.is_valid(raise_exception=True):
            # The serializer's .update() method saves the new email and sets user inactive
            serializer.save() 
            
            # --- Now, send a new verification email ---
            user = self.object
            token = issue_user_action_token(
                user=user,
                purpose=EMAIL_VERIFICATION_PURPOSE,
                lifetime_minutes=_token_minutes("EMAIL_VERIFICATION_TOKEN_MINUTES", 60),
            )
            
            frontend_url = get_frontend_url()
            verification_link = f"{frontend_url}/verify-email/confirm/{token}"
            
            subject = 'Please Verify Your New Email Address'
            context = {'username': user.username, 'verification_link': verification_link}
            html_message = render_to_string('emails/verification_email.html', context)
            plain_message = strip_tags(html_message)
            
            try:
                send_mail(subject, plain_message, get_default_from_email(), [user.email], html_message=html_message)
            except Exception:
                logger.exception("Failed to send re-verification email for user_id=%s", user.id)
                # We don't fail the whole request, just log the email error
            
            return Response({"message": "Email updated successfully. Please check your new email address to re-verify your account."}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class DeleteAccountView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = DeleteAccountSerializer

    def delete(self, request):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = request.user
            user.delete()
            return Response(
                {"message": "Account deleted successfully."},
                status=status.HTTP_204_NO_CONTENT
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if _is_cookie_mode_enabled():
            access_cookie_name = getattr(settings, "JWT_ACCESS_COOKIE_NAME", "openeire_access")
            refresh_cookie_name = getattr(settings, "JWT_REFRESH_COOKIE_NAME", "openeire_refresh")
            has_auth_cookies = bool(
                request.COOKIES.get(access_cookie_name) or request.COOKIES.get(refresh_cookie_name)
            )
            if has_auth_cookies:
                _enforce_cookie_csrf(request)
        response = Response({"message": "Logged out."}, status=status.HTTP_200_OK)
        _clear_jwt_cookies(response)
        return response

class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    callback_url = "postmessage"
    client_class = OAuth2Client

    def post(self, request, *args, **kwargs):
        has_settings_app = bool(
            getattr(settings, "SOCIALACCOUNT_PROVIDERS", {})
            .get("google", {})
            .get("APP")
        )
        try:
            response = super().post(request, *args, **kwargs)
        except SocialApp.DoesNotExist:
            logger.exception(
                "Google login attempted without an allauth SocialApp configuration. "
                "settings_app_present=%s",
                has_settings_app,
            )
            return Response(
                {"detail": "Google login is not configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except SocialApp.MultipleObjectsReturned:
            logger.exception(
                "Google login attempted with duplicate allauth app configuration. "
                "settings_app_present=%s",
                has_settings_app,
            )
            if has_settings_app:
                detail = (
                    "Google login is misconfigured on the server. "
                    "Configure either a Google SocialApp or env-based Google OAuth settings, not both."
                )
            else:
                detail = (
                    "Google login is misconfigured on the server. "
                    "Multiple Google SocialApp records were found; keep only one Google SocialApp entry."
                )
            return Response(
                {"detail": detail},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if response.status_code != 200:
            logger.warning("Google login failed with status_code=%s", response.status_code)
        return response

class CountryListView(APIView):
    """
    Returns a list of all countries for the frontend dropdown.
    """
    permission_classes = [AllowAny] # Allow anyone to see the country list

    def get(self, request):
        # countries is an iterator of (code, name) tuples
        country_list = [
            {'code': code, 'name': name} 
            for code, name in list(countries)
        ]
        return Response(country_list)
