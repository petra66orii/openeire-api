from rest_framework import serializers

from .models import Video, VideoUploadSession
from .uploads import get_allowed_video_content_types, get_max_video_upload_size


class VideoUploadStartSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=100)
    file_size = serializers.IntegerField(min_value=1)
    purpose = serializers.ChoiceField(choices=VideoUploadSession.PURPOSE_CHOICES)
    target_video_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_content_type(self, value):
        normalized = str(value).strip().lower()
        if normalized not in get_allowed_video_content_types():
            raise serializers.ValidationError("Unsupported video content type.")
        return normalized

    def validate_file_size(self, value):
        max_file_size = get_max_video_upload_size()
        if value > max_file_size:
            raise serializers.ValidationError(
                f"Video file exceeds the maximum allowed size of {max_file_size} bytes."
            )
        return value

    def validate_target_video_id(self, value):
        if value is None:
            return value
        if not Video.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Selected video does not exist.")
        return value


class VideoUploadPartUrlSerializer(serializers.Serializer):
    upload_id = serializers.CharField(max_length=VideoUploadSession.MAX_UPLOAD_ID_LENGTH)
    object_key = serializers.CharField(max_length=500)
    part_number = serializers.IntegerField(min_value=1)


class CompletedUploadPartSerializer(serializers.Serializer):
    part_number = serializers.IntegerField(min_value=1)
    etag = serializers.CharField(max_length=255)

    def validate_etag(self, value):
        candidate = str(value).strip()
        if not candidate:
            raise serializers.ValidationError("ETag is required.")
        return candidate


class VideoUploadCompleteSerializer(serializers.Serializer):
    upload_id = serializers.CharField(max_length=VideoUploadSession.MAX_UPLOAD_ID_LENGTH)
    object_key = serializers.CharField(max_length=500)
    parts = CompletedUploadPartSerializer(many=True, allow_empty=False)


class VideoUploadAbortSerializer(serializers.Serializer):
    upload_id = serializers.CharField(max_length=VideoUploadSession.MAX_UPLOAD_ID_LENGTH)
    object_key = serializers.CharField(max_length=500)


class VideoUploadTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = ("id", "title", "collection", "is_active")
