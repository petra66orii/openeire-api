from django.urls import path

from .views import RealEstateDepositCancelledView, RealEstateEnquiryCreateView


urlpatterns = [
    path("enquiries/", RealEstateEnquiryCreateView.as_view(), name="real-estate-enquiry-create"),
    path(
        "deposit/cancelled/",
        RealEstateDepositCancelledView.as_view(),
        name="real-estate-deposit-cancelled",
    ),
]

