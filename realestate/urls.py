from django.urls import path

from .views import RealEstateEnquiryCreateView


urlpatterns = [
    path("enquiries/", RealEstateEnquiryCreateView.as_view(), name="real-estate-enquiry-create"),
]

