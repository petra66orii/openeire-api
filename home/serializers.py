from rest_framework import serializers
from .models import Testimonial, NewsletterSubscriber

class TestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Testimonial
        fields = ('id', 'name', 'text', 'rating')

class NewsletterSubscriberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(validators=[])
    first_name = serializers.CharField(required=False, allow_blank=True, default="")
    source = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = NewsletterSubscriber
        fields = (
            'id',
            'email',
            'first_name',
            'source',
            'brevo_synced_at',
            'brevo_sync_status',
            'brevo_sync_error',
        )
        read_only_fields = ('brevo_synced_at', 'brevo_sync_status', 'brevo_sync_error')

    def create(self, validated_data):
        email = str(validated_data.get("email") or "").strip().lower()
        defaults = {
            "first_name": str(validated_data.get("first_name") or "").strip(),
            "source": str(validated_data.get("source") or "").strip(),
        }
        subscriber, created = NewsletterSubscriber.objects.get_or_create(
            email=email,
            defaults=defaults,
        )
        if not created:
            update_fields = []
            for field, value in defaults.items():
                if value and getattr(subscriber, field) != value:
                    setattr(subscriber, field, value)
                    update_fields.append(field)
            if update_fields:
                subscriber.save(update_fields=update_fields)
        return subscriber


class ContactFormSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    email = serializers.EmailField()
    subject = serializers.CharField(max_length=200)
    message = serializers.CharField(max_length=2000)

    def validate_subject(self, value):
        # Optional: Add specific logic if you want to restrict subjects
        return value
