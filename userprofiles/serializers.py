from rest_framework import serializers
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password as django_validate_password
from .models import UserProfile
from django_countries.serializer_fields import CountryField
from django.contrib.auth import authenticate
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

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
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

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
        user.save()
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
    
    # Use the special serializer field for the country
    country = CountryField(source='default_country', country_dict=True)

    class Meta:
        model = UserProfile
        fields = (
            'username',
            'first_name',
            'last_name',
            'email',
            'default_phone_number',
            'default_street_address1',
            'default_street_address2',
            'default_town',
            'default_county',
            'default_postcode',
            'country',
        )

    def update(self, instance, validated_data):
        # Handle User data update if provided
        user_data = validated_data.pop('user', {})
        if user_data:
            user = instance.user
            user.username = user_data.get('username', user.username)
            user.first_name = user_data.get('first_name', user.first_name)
            user.last_name = user_data.get('last_name', user.last_name)
            user.email = user_data.get('email', user.email)
            user.save()

            country_data = validated_data.pop('country', None)
        if country_data:
            # When updating, CountryField(country_dict=True) might give a dict,
            # we need the country code for the model.
            instance.default_country = country_data.get('code') if isinstance(country_data, dict) else country_data

        # Handle UserProfile data update
        return super().update(instance, validated_data)


# --- (Password Reset Serializers remain the same) ---
class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    def validate_email(self, value):
        try: User.objects.get(email=value)
        except User.DoesNotExist: raise serializers.ValidationError("User with this email does not exist.")
        return value

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
        try:
            user = User.objects.get(email=value)
            if user.is_active:
                raise serializers.ValidationError("This account is already verified.")
        except User.DoesNotExist:
            raise serializers.ValidationError("No account found with this email address.")
        return value

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom token serializer to allow login with either username or email.
    """
    def validate(self, attrs):
        # Get the identifier (could be username or email) and password
        identifier = attrs.get(self.username_field)
        password = attrs.get('password')

        user = None
        # Try authenticating with the identifier directly as username
        user_by_username = authenticate(request=self.context.get('request'), username=identifier, password=password)

        if user_by_username:
            user = user_by_username
        else:
            # If username auth failed, try finding the user by email
            try:
                user_obj = User.objects.get(email__iexact=identifier)
                # Then authenticate using the found user's actual username
                user_by_email = authenticate(request=self.context.get('request'), username=user_obj.username, password=password)
                if user_by_email:
                    user = user_by_email
            except User.DoesNotExist:
                # No user with this email exists, authentication fails
                pass

        # If no user was found by either method or if the user is inactive
        if not user or not user.is_active:
            raise serializers.ValidationError('No active account found with the given credentials')

        # If authentication succeeds, proceed with token generation
        refresh = self.get_token(user)

        data = {}
        data['refresh'] = str(refresh)
        data['access'] = str(refresh.access_token)

        return data