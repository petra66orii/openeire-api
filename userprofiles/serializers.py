# userprofiles/serializers.py

from rest_framework import serializers
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password as django_validate_password
from .models import UserProfile
from django_countries.serializer_fields import CountryField

class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for the User model, specifically for registration.
    """
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password') # Removed country, corrected sources
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        user = User(
            email=validated_data['email'],
            username=validated_data['username']
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
    email = serializers.EmailField(source='user.email')
    
    # Use the special serializer field for the country
    country = CountryField(source='default_country', country_dict=True)

    class Meta:
        model = UserProfile
        fields = (
            'username',
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