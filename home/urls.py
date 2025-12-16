from django.urls import path
from .views import TestimonialListView, NewsletterSignupView, ContactFormView

urlpatterns = [
    path('testimonials/', TestimonialListView.as_view(), name='testimonial_list'),
    path('newsletter-signup/', NewsletterSignupView.as_view(), name='newsletter_signup'),
    path('contact/', ContactFormView.as_view(), name='contact_form'),
]