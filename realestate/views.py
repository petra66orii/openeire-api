import logging

from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from openeire_api.throttling import SharedScopedRateThrottle

from .emails import (
    send_realestate_client_confirmation_email,
    send_realestate_internal_notification_email,
)
from .models import RealEstateEnquiry
from .serializers import RealEstateEnquirySerializer


logger = logging.getLogger(__name__)


class RealEstateEnquiryCreateView(generics.CreateAPIView):
    queryset = RealEstateEnquiry.objects.all()
    serializer_class = RealEstateEnquirySerializer
    permission_classes = [AllowAny]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = "real_estate_enquiry"

    def perform_create(self, serializer):
        self.enquiry = serializer.save()

    def _send_emails(self, enquiry):
        try:
            send_realestate_internal_notification_email(enquiry, request=self.request)
        except Exception:
            logger.exception(
                "Failed to send internal real estate enquiry notification. enquiry_id=%s",
                enquiry.id,
            )
        try:
            send_realestate_client_confirmation_email(enquiry)
        except Exception:
            logger.exception(
                "Failed to send real estate enquiry confirmation email. enquiry_id=%s",
                enquiry.id,
            )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        self._send_emails(self.enquiry)
        return Response(
            {
                "id": self.enquiry.id,
                "status": self.enquiry.status,
                "message": "Enquiry received successfully.",
            },
            status=status.HTTP_201_CREATED,
        )

