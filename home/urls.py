from django.urls import path
from .views import TestimonialListView

urlpatterns = [
    path('testimonials/', TestimonialListView.as_view(), name='testimonial_list'),
]