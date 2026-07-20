from django.urls import path

from .views import (
    RealEstateDepositCancelledView,
    RealEstateDepositSuccessView,
    RealEstateEnquiryCreateView,
)


urlpatterns = [
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

