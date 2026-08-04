from rest_framework import serializers

from .models import (
    RealEstateDeliverable,
    RealEstateDelivery,
    RealEstateDeliveryUploadSession,
)
from .delivery_storage import get_max_files, get_max_size, validate_upload


class DeliveryExchangeSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    secret = serializers.CharField(max_length=2048, trim_whitespace=False)


class DeliverySessionSerializer(serializers.Serializer):
    session = serializers.CharField(max_length=2048, trim_whitespace=False)


class DeliveryDownloadSerializer(DeliverySessionSerializer):
    deliverable_id = serializers.UUIDField()


class DeliveryUploadStartSerializer(serializers.Serializer):
    delivery_id = serializers.IntegerField(min_value=1)
    filename = serializers.CharField(max_length=255)
    display_name = serializers.CharField(max_length=255)
    category = serializers.ChoiceField(choices=RealEstateDeliverable.Category.choices)
    content_type = serializers.CharField(max_length=100, allow_blank=True)
    file_size = serializers.IntegerField(min_value=1, max_value=get_max_size())
    sort_order = serializers.IntegerField(min_value=0, default=0)
    replaces_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        safe_name, canonical_type = validate_upload(
            attrs["filename"],
            attrs["content_type"],
            attrs["file_size"],
            attrs["category"],
        )
        attrs["filename"] = safe_name
        attrs["content_type"] = canonical_type
        delivery = RealEstateDelivery.objects.filter(pk=attrs["delivery_id"]).first()
        if not delivery:
            raise serializers.ValidationError("Selected delivery does not exist.")
        attrs["delivery"] = delivery
        replacement_id = attrs.get("replaces_id")
        if replacement_id:
            replacement = RealEstateDeliverable.objects.filter(
                public_id=replacement_id,
                delivery=delivery,
                is_active=True,
            ).first()
            if not replacement:
                raise serializers.ValidationError("Replacement target is unavailable.")
            attrs["replacement"] = replacement
        else:
            attrs["replacement"] = None
            active_files = delivery.deliverables.filter(
                is_active=True,
                deleted_at__isnull=True,
            ).count()
            reserved_uploads = delivery.upload_sessions.filter(
                replaces__isnull=True,
                status__in=(
                    RealEstateDeliveryUploadSession.Status.INITIATED,
                    RealEstateDeliveryUploadSession.Status.COMPLETING,
                ),
            ).count()
            if active_files + reserved_uploads >= get_max_files():
                raise serializers.ValidationError(
                    "This delivery has reached its configured file limit."
                )
        return attrs


class DeliveryUploadSessionSerializer(serializers.Serializer):
    upload_id = serializers.CharField(
        max_length=RealEstateDeliveryUploadSession._meta.get_field("upload_id").max_length
    )


class DeliveryUploadPartSerializer(DeliveryUploadSessionSerializer):
    part_number = serializers.IntegerField(min_value=1, max_value=10_000)


class CompletedPartSerializer(serializers.Serializer):
    part_number = serializers.IntegerField(min_value=1, max_value=10_000)
    etag = serializers.CharField(max_length=255)

    def validate_etag(self, value):
        value = str(value).strip()
        if not value:
            raise serializers.ValidationError("ETag is required.")
        return value


class DeliveryUploadCompleteSerializer(DeliveryUploadSessionSerializer):
    parts = CompletedPartSerializer(many=True, allow_empty=False)
