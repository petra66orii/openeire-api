import logging

from django.conf import settings
from django.views.generic import TemplateView
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from openeire_api.throttling import SharedScopedRateThrottle

from .emails import (
    get_realestate_reply_to_email,
    send_realestate_client_confirmation_email,
    send_realestate_internal_notification_email,
)
from .models import RealEstateEnquiry
from .serializers import RealEstateEnquirySerializer
from .timeline import record_timeline_event


logger = logging.getLogger(__name__)


class RealEstateDepositCancelledView(TemplateView):
    template_name = "realestate/deposit_cancelled.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "frontend_url": str(
                    getattr(settings, "FRONTEND_URL", None) or "https://openeire.ie"
                ).rstrip("/"),
                "contact_email": get_realestate_reply_to_email(),
            }
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        response["Cache-Control"] = "no-store"
        return response


class RealEstateDepositSuccessView(RealEstateDepositCancelledView):
    template_name = "realestate/deposit_success.html"


class RealEstateEnquiryCreateView(generics.CreateAPIView):
    queryset = RealEstateEnquiry.objects.all()
    serializer_class = RealEstateEnquirySerializer
    permission_classes = [AllowAny]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = "real_estate_enquiry"

    def perform_create(self, serializer):
        self.enquiry = serializer.save()
        notes = []
        if self.enquiry.preferred_package:
            notes.append(
                f"Preferred package: {self.enquiry.get_preferred_package_display()}"
            )
        if self.enquiry.property_address:
            notes.append(f"Property address: {self.enquiry.property_address}")
        record_timeline_event(
            self.enquiry,
            "enquiry_received",
            status="completed",
            actor_type="client",
            title="Enquiry received",
            notes="\n".join(notes),
        )

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

