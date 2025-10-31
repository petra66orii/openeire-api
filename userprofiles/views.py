import jwt
from django.conf import settings
from rest_framework import generics, status, serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from .serializers import UserSerializer, PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from .models import UserProfile
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import (
    UserProfileSerializer,
    ResendVerificationSerializer,
    MyTokenObtainPairSerializer,
    ChangePasswordSerializer,
    ChangeEmailSerializer
)

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = UserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # Generate a token for the user
        refresh = RefreshToken.for_user(user)
        token = str(refresh.access_token)
        
        # --- Email Sending Logic ---
        frontend_url = 'http://localhost:5173'
        verification_link = f"{frontend_url}/verify-email/confirm/{token}"
        
        subject = 'Welcome to OpenEire Studios! Verify Your Email'
        context = {
            'username': user.username,
            'verification_link': verification_link
        }
        
        html_message = render_to_string('emails/verification_email.html', context)
        plain_message = strip_tags(html_message)
        from_email = settings.DEFAULT_FROM_EMAIL
        to_email = user.email

        try:
            send_mail(subject, plain_message, from_email, [to_email], html_message=html_message)
            print(f"--- Verification email sent to {user.email} ---")
        except Exception as e:
            print(f"--- FAILED to send email: {e} ---")
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
            # Decode the token to get the user ID
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user_id = payload['user_id']
            user = User.objects.get(id=user_id)

            if user.is_active:
                return Response({'message': 'Account already verified.'}, status=status.HTTP_200_OK)

            user.is_active = True
            user.save()
            return Response({'message': 'Email successfully verified!'}, status=status.HTTP_200_OK)

        except jwt.ExpiredSignatureError:
            return Response({'error': 'Activation link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except (jwt.exceptions.DecodeError, User.DoesNotExist):
            return Response({'error': 'Invalid activation link.'}, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetRequestView(generics.GenericAPIView):
    """
    API endpoint to request a password reset.
    """
    serializer_class = PasswordResetRequestSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        user = User.objects.get(email=email)

        # Generate a short-lived token for the user
        refresh = RefreshToken.for_user(user)
        token = str(refresh.access_token)
        
        # TODO: Send an email with a link like /password-reset/confirm/{token}
        print(f"--- PASSWORD RESET TOKEN FOR {user.email}: {token} ---")
        
        return Response({"message": "Password reset link sent."}, status=status.HTTP_200_OK)


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
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user = User.objects.get(id=payload['user_id'])
            user.set_password(password)
            user.save()
            return Response({"message": "Password reset successful."}, status=status.HTTP_200_OK)
        except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError, User.DoesNotExist):
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

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        user = User.objects.get(email=email)

        # Generate a new token
        refresh = RefreshToken.for_user(user)
        token = str(refresh.access_token)

        # Resend the email (reuse logic from RegisterView)
        frontend_url = 'http://localhost:5173'
        verification_link = f"{frontend_url}/verify-email/confirm/{token}"
        subject = 'Verify Your OpenEire Studios Email Address'
        context = {'username': user.username, 'verification_link': verification_link}
        html_message = render_to_string('emails/verification_email.html', context)
        plain_message = strip_tags(html_message)
        from_email = settings.DEFAULT_FROM_EMAIL
        to_email = user.email

        try:
            send_mail(subject, plain_message, from_email, [to_email], html_message=html_message)
            print(f"--- Re-sent verification email to {user.email} ---")
            return Response({"message": "Verification email sent."}, status=status.HTTP_200_OK)
        except Exception as e:
            print(f"--- FAILED to resend email: {e} ---")
            return Response({"error": "Failed to send email."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MyTokenObtainPairView(TokenObtainPairView):
    """
    Custom view using the custom serializer to allow email/username login.
    """
    serializer_class = MyTokenObtainPairSerializer


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