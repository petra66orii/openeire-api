from rest_framework import serializers
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.contrib.auth.password_validation import validate_password as django_validate_password
from .models import UserProfile
from django_countries.serializer_fields import CountryField
from django.contrib.auth import authenticate
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


def _normalize_email(value):
    return str(value).strip().lower()


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for the User model, specifically for registration.
    """
    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'email', 'password')
        extra_kwargs = {
            'password': {'write_only': True},
            'first_name': {'required': False, 'allow_blank': True},
            'last_name': {'required': False, 'allow_blank': True},
        }

    def validate_email(self, value):
        """
        Check that the email is not already in use.
        """
        normalized = _normalize_email(value)
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return normalized

    def validate_username(self, value):
        """
        Check that the username is not already in use.
        """
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def create(self, validated_data):
        user = User(
            email=validated_data['email'],
            username=validated_data['username'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', '')
        )
        user.set_password(validated_data['password'])
        user.is_active = False
        try:
            user.save()
        except IntegrityError:
            errors = {}
            email = validated_data.get("email")
            username = validated_data.get("username")
            if email and User.objects.filter(email__iexact=email).exists():
                errors["email"] = "A user with this email already exists."
            if username and User.objects.filter(username__iexact=username).exists():
                errors["username"] = "A user with that username already exists."
            if not errors:
                errors["non_field_errors"] = [
                    "Could not create account due to a data integrity conflict. Please retry."
                ]
            raise serializers.ValidationError(errors)
        return user

class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for the UserProfile model for viewing and updating.
    """
    # Get username and email from the related User object
    username = serializers.CharField(source='user.username')
    first_name = serializers.CharField(source='user.first_name')
    last_name = serializers.CharField(source='user.last_name')
    email = serializers.EmailField(source='user.email')
    is_staff = serializers.BooleanField(source='user.is_staff', read_only=True)
    
    # Use the special serializer field for the country
    country = CountryField(source='default_country', name_only=True)

    class Meta:
        model = UserProfile
        fields = (
            'username',
            'first_name',
            'last_name',
            'email',
            'is_staff',
            'default_phone_number',
            'default_street_address1',
            'default_street_address2',
            'default_town',
            'default_county',
            'default_postcode',
            'country',
            'can_access_gallery',
        )

    def update(self, instance, validated_data):
        """
        Custom update method to handle nested User object and UserProfile.
        """
        # 1. Pop the nested 'user' dictionary entirely.
        # This prevents the 'dotted-source' error in the default update method.
        user_data = validated_data.pop('user', {})

        # 2. Update the User model manually
        user = instance.user
        
        # Loop through whatever user fields were sent (username, email, etc.)
        for attr, value in user_data.items():
            if attr == "email":
                value = _normalize_email(value)
            setattr(user, attr, value)

        try:
            user.save()
        except IntegrityError:
            errors = {}
            email = user_data.get("email")
            username = user_data.get("username")
            if email and User.objects.filter(email__iexact=_normalize_email(email)).exclude(pk=user.pk).exists():
                errors["email"] = "This email is already in use by another account."
            if username and User.objects.filter(username__iexact=username).exclude(pk=user.pk).exists():
                errors["username"] = "A user with that username already exists."
            if not errors:
                errors["non_field_errors"] = [
                    "Could not update profile due to a data integrity conflict. Please retry."
                ]
            raise serializers.ValidationError(errors)

        # 3. Update UserProfile fields using the default Django logic
        # Since 'user' data is gone from validated_data, this won't crash.
        return super().update(instance, validated_data)

# --- (Password Reset Serializers remain the same) ---
class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return _normalize_email(value)

class PasswordResetConfirmSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, required=True, validators=[django_validate_password])
    confirm_password = serializers.CharField(write_only=True, required=True)
    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']: raise serializers.ValidationError({"password": "Passwords do not match."})
        return attrs

class ResendVerificationSerializer(serializers.Serializer):
    """Serializer for requesting a new verification email."""
    email = serializers.EmailField()

    def validate_email(self, value):
        return _normalize_email(value)

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom token serializer to allow login with either username or email.
    """
    def validate(self, attrs):
        # Get the identifier (could be username or email) and password
        identifier = attrs.get(self.username_field)
        password = attrs.get('password')

        user = None
        request = self.context.get('request')
        normalized_identifier = (identifier or "").strip()
        allow_username_fallback = True

        # If identifier looks like an email, enforce unique-match semantics first.
        if "@" in normalized_identifier:
            candidates = list(User.objects.filter(email__iexact=normalized_identifier).only("username")[:2])
            if len(candidates) == 1:
                user_obj = candidates[0]
                user_by_email = authenticate(
                    request=request,
                    username=user_obj.username,
                    password=password,
                )
                if user_by_email:
                    user = user_by_email
            elif len(candidates) > 1:
                allow_username_fallback = False

        # Always fall back to username authentication.
        # This preserves support for email-shaped usernames.
        if user is None and allow_username_fallback:
            user_by_username = authenticate(
                request=request,
                username=normalized_identifier,
                password=password,
            )
            if user_by_username:
                user = user_by_username

        # If no user was found by either method or if the user is inactive
        if not user or not user.is_active:
            raise serializers.ValidationError('No active account found with the given credentials')

        # If authentication succeeds, proceed with token generation
        self.user = user
        refresh = self.get_token(user)

        data = {}
        data['refresh'] = str(refresh)
        data['access'] = str(refresh.access_token)

        return data

class ChangePasswordSerializer(serializers.Serializer):
    """
    Serializer for password change endpoint.
    """
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)

    def validate_new_password(self, value):
        # Run the new password through Django's built-in validators
        django_validate_password(value, self.context['request'].user)
        return value

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Your old password was entered incorrectly. Please try again.")
        return value

    def update(self, instance, validated_data):
        # set_password hashes the password
        instance.set_password(validated_data['new_password'])
        instance.save()
        return instance

    def create(self, validated_data):
        # This serializer is only for updating, not creating
        raise NotImplementedError()

class ChangeEmailSerializer(serializers.Serializer):
    """
    Serializer for email change endpoint.
    """
    new_email = serializers.EmailField(required=True)
    current_password = serializers.CharField(required=True)

    def validate_new_email(self, value):
        """
        Check that the new email is not already in use.
        """
        normalized = _normalize_email(value)
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError("This email is already in use by another account.")
        return normalized

    def validate_current_password(self, value):
        """
        Validate the user's current password.
        """
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Your old password was entered incorrectly. Please try again.")
        return value

    def update(self, instance, validated_data):
        """
        Update the user's email.
        """
        instance.email = validated_data['new_email']
        
        # We also set the user to 'inactive' to force re-verification of the new email.
        instance.is_active = False 
        try:
            instance.save(update_fields=['email', 'username', 'is_active'])
        except IntegrityError:
            raise serializers.ValidationError(
                {"new_email": "This email is already in use by another account."}
            )
        return instance

    def create(self, validated_data):
        raise NotImplementedError()

class DeleteAccountSerializer(serializers.Serializer):
    password = serializers.CharField(required=True)

    def validate_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Incorrect password.")
        return value
