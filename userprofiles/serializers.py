from rest_framework import serializers
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password as django_validate_password

class UserSerializer(serializers.ModelSerializer):
    """Serializer for the User model, handles registration."""
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password')
        extra_kwargs = {'password': {'write_only': True}}

    def validate_password(self, value):
        """
        Validate the password using Django's built-in validators
        and add custom rules.
        """
        if len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters long.")
        # Attempt to use Django's built-in validation for common patterns
        # This will check against common passwords, username, etc.
        try:
            django_validate_password(value, user=self.initial_data.get('username')) # Pass username for validation
        except ValidationError as e:
            # Django's validator raises ValidationError, convert it to DRF's
            raise serializers.ValidationError(e.messages)

        return value

    def create(self, validated_data):
        """
        Create and return a new user, with a hashed password.
        """
        user = User(
            email=validated_data['email'],
            username=validated_data['username']
        )
        user.set_password(validated_data['password'])
        # is_active=False until they verify their email
        user.is_active = False
        user.save()
        return user