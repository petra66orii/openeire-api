from django.urls import path

from .views import (
    RealEstateDepositCancelledView,
    RealEstateDepositSuccessView,
    RealEstateEnquiryCreateView,
)
from .delivery_views import (
    DeliveryDownloadView,
    DeliveryExchangeView,
    DeliverySessionView,
    StaffDeliveryUploadAbortView,
    StaffDeliveryUploadCompleteView,
    StaffDeliveryUploadPartView,
    StaffDeliveryUploadStartView,
)


urlpatterns = [
    path("delivery/exchange/", DeliveryExchangeView.as_view(), name="delivery-exchange"),
    path("delivery/session/", DeliverySessionView.as_view(), name="delivery-session"),
    path("delivery/download/", DeliveryDownloadView.as_view(), name="delivery-download"),
    path(
        "delivery/uploads/start/",
        StaffDeliveryUploadStartView.as_view(),
        name="delivery-upload-start",
    ),
    path(
        "delivery/uploads/part-url/",
        StaffDeliveryUploadPartView.as_view(),
        name="delivery-upload-part",
    ),
    path(
        "delivery/uploads/complete/",
        StaffDeliveryUploadCompleteView.as_view(),
        name="delivery-upload-complete",
    ),
    path(
        "delivery/uploads/abort/",
        StaffDeliveryUploadAbortView.as_view(),
        name="delivery-upload-abort",
    ),
    path("enquiries/", RealEstateEnquiryCreateView.as_view(), name="real-estate-enquiry-create"),
    path(
        "deposit/cancelled/",
        RealEstateDepositCancelledView.as_view(),
        name="real-estate-deposit-cancelled",
    ),
    path(
        "deposit/success/",
        RealEstateDepositSuccessView.as_view(),
        name="real-estate-deposit-success",
    ),
]

