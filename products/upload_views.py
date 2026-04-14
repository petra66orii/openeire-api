import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Video, VideoUploadSession
from .permissions import IsStaffUser
from .upload_serializers import (
    VideoUploadAbortSerializer,
    VideoUploadCompleteSerializer,
    VideoUploadPartUrlSerializer,
    VideoUploadStartSerializer,
    VideoUploadTargetSerializer,
)
from .uploads import (
    abort_multipart_upload,
    complete_multipart_upload,
    generate_part_upload_url,
    start_multipart_upload,
)

logger = logging.getLogger(__name__)


def _get_upload_session_or_404(*, request_user, upload_id, object_key):
    return generics.get_object_or_404(
        VideoUploadSession,
        upload_id=upload_id,
        object_key=object_key,
        created_by=request_user,
    )


class StaffVideoTargetListView(generics.ListAPIView):
    permission_classes = [IsStaffUser]
    serializer_class = VideoUploadTargetSerializer

    def get_queryset(self):
        query = (self.request.query_params.get("query") or "").strip()
        queryset = Video.objects.all().order_by("title")
        if query:
            queryset = queryset.filter(title__icontains=query)
        return queryset[:25]


class StartVideoMultipartUploadView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = VideoUploadStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        target_video = None
        target_video_id = validated.get("target_video_id")
        if target_video_id:
            target_video = Video.objects.filter(pk=target_video_id).first()

        upload_details = start_multipart_upload(
            filename=validated["filename"],
            content_type=validated["content_type"],
            file_size=validated["file_size"],
            purpose=validated["purpose"],
        )

        try:
            VideoUploadSession.objects.create(
                created_by=request.user,
                target_video=target_video,
                original_filename=validated["filename"],
                object_key=upload_details["object_key"],
                upload_id=upload_details["upload_id"],
                purpose=validated["purpose"],
                status=VideoUploadSession.STATUS_INITIATED,
                file_size=validated["file_size"],
                content_type=validated["content_type"],
                part_size=upload_details["part_size"],
            )
        except Exception:
            logger.exception(
                "Failed to persist multipart upload session. upload_id=%s object_key=%s",
                upload_details["upload_id"],
                upload_details["object_key"],
            )
            try:
                abort_multipart_upload(
                    purpose=validated["purpose"],
                    upload_id=upload_details["upload_id"],
                    object_key=upload_details["object_key"],
                )
            except Exception:
                logger.exception(
                    "Failed to clean up orphaned multipart upload after session create failure. upload_id=%s object_key=%s",
                    upload_details["upload_id"],
                    upload_details["object_key"],
                )
            raise

        return Response(
            {
                "upload_id": upload_details["upload_id"],
                "object_key": upload_details["object_key"],
                "bucket": upload_details["bucket_name"],
                "part_size": upload_details["part_size"],
                "max_concurrency": upload_details["max_concurrency"],
                "purpose": validated["purpose"],
                "target_video_id": target_video_id,
            },
            status=status.HTTP_201_CREATED,
        )


class VideoMultipartPartUrlView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = VideoUploadPartUrlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = _get_upload_session_or_404(
            request_user=request.user,
            upload_id=serializer.validated_data["upload_id"],
            object_key=serializer.validated_data["object_key"],
        )
        if session.status != VideoUploadSession.STATUS_INITIATED:
            return Response(
                {"detail": "Multipart upload is not active."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        upload_url = generate_part_upload_url(
            purpose=session.purpose,
            upload_id=session.upload_id,
            object_key=session.object_key,
            part_number=serializer.validated_data["part_number"],
        )
        return Response({"url": upload_url}, status=status.HTTP_200_OK)


class CompleteVideoMultipartUploadView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = VideoUploadCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = _get_upload_session_or_404(
            request_user=request.user,
            upload_id=serializer.validated_data["upload_id"],
            object_key=serializer.validated_data["object_key"],
        )

        with transaction.atomic():
            session = VideoUploadSession.objects.select_for_update().get(pk=session.pk)
            if session.status == VideoUploadSession.STATUS_COMPLETED:
                return Response(
                    {
                        "success": True,
                        "object_key": session.object_key,
                        "status": session.status,
                        "video_id": session.target_video_id,
                    },
                    status=status.HTTP_200_OK,
                )
            if session.status != VideoUploadSession.STATUS_INITIATED:
                return Response(
                    {"detail": "Multipart upload is not active."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            session.status = VideoUploadSession.STATUS_COMPLETING
            session.error_message = ""
            session.save(update_fields=["status", "error_message", "updated_at"])

        try:
            complete_multipart_upload(
                purpose=session.purpose,
                upload_id=session.upload_id,
                object_key=session.object_key,
                parts=serializer.validated_data["parts"],
            )
        except Exception:
            logger.exception(
                "Failed to complete multipart upload. upload_id=%s object_key=%s",
                session.upload_id,
                session.object_key,
            )
            with transaction.atomic():
                session = VideoUploadSession.objects.select_for_update().get(pk=session.pk)
                if session.status == VideoUploadSession.STATUS_COMPLETING:
                    session.status = VideoUploadSession.STATUS_FAILED
                    session.error_message = "Failed to complete multipart upload."
                    session.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {"detail": "Failed to complete multipart upload."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        with transaction.atomic():
            session = VideoUploadSession.objects.select_for_update().get(pk=session.pk)
            if session.status == VideoUploadSession.STATUS_COMPLETED:
                return Response(
                    {
                        "success": True,
                        "object_key": session.object_key,
                        "status": session.status,
                        "video_id": session.target_video_id,
                    },
                    status=status.HTTP_200_OK,
                )

            if session.status != VideoUploadSession.STATUS_COMPLETING:
                return Response(
                    {"detail": "Multipart upload is not active."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            session.status = VideoUploadSession.STATUS_COMPLETED
            session.error_message = ""
            session.completed_at = timezone.now()
            session.save(
                update_fields=["status", "error_message", "completed_at", "updated_at"]
            )

            if session.target_video_id:
                target_video = session.target_video
                if session.purpose == VideoUploadSession.PURPOSE_PREVIEW:
                    target_video.preview_video_key = session.object_key
                    target_video.save(update_fields=["preview_video_key"])
                else:
                    target_video.video_file_key = session.object_key
                    target_video.save(update_fields=["video_file_key"])

        return Response(
            {
                "success": True,
                "object_key": session.object_key,
                "status": session.status,
                "video_id": session.target_video_id,
            },
            status=status.HTTP_200_OK,
        )


class AbortVideoMultipartUploadView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = VideoUploadAbortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = _get_upload_session_or_404(
            request_user=request.user,
            upload_id=serializer.validated_data["upload_id"],
            object_key=serializer.validated_data["object_key"],
        )

        with transaction.atomic():
            session = VideoUploadSession.objects.select_for_update().get(pk=session.pk)
            if session.status == VideoUploadSession.STATUS_ABORTED:
                return Response({"success": True, "status": session.status}, status=status.HTTP_200_OK)
            if session.status == VideoUploadSession.STATUS_COMPLETED:
                return Response(
                    {"detail": "Completed uploads cannot be aborted."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if session.status != VideoUploadSession.STATUS_INITIATED:
                return Response(
                    {"detail": "Multipart upload is not active."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            session.status = VideoUploadSession.STATUS_ABORTING
            session.error_message = ""
            session.save(update_fields=["status", "error_message", "updated_at"])

        try:
            abort_multipart_upload(
                purpose=session.purpose,
                upload_id=session.upload_id,
                object_key=session.object_key,
            )
        except Exception:
            logger.exception(
                "Failed to abort multipart upload. upload_id=%s object_key=%s",
                session.upload_id,
                session.object_key,
            )
            with transaction.atomic():
                session = VideoUploadSession.objects.select_for_update().get(pk=session.pk)
                if session.status == VideoUploadSession.STATUS_ABORTING:
                    session.status = VideoUploadSession.STATUS_FAILED
                    session.error_message = "Failed to abort multipart upload."
                    session.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {"detail": "Failed to abort multipart upload."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        with transaction.atomic():
            session = VideoUploadSession.objects.select_for_update().get(pk=session.pk)
            if session.status == VideoUploadSession.STATUS_ABORTED:
                return Response({"success": True, "status": session.status}, status=status.HTTP_200_OK)

            if session.status != VideoUploadSession.STATUS_ABORTING:
                return Response(
                    {"detail": "Multipart upload is not active."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            session.status = VideoUploadSession.STATUS_ABORTED
            session.error_message = ""
            session.aborted_at = timezone.now()
            session.save(update_fields=["status", "error_message", "aborted_at", "updated_at"])
            return Response({"success": True, "status": session.status}, status=status.HTTP_200_OK)
