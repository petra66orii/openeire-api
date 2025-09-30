from rest_framework import generics
from rest_framework.permissions import AllowAny
from .models import Testimonial, NewsletterSubscriber
from .serializers import TestimonialSerializer, NewsletterSubscriberSerializer

class TestimonialListView(generics.ListAPIView):
    """
    API endpoint to list all testimonials.
    """
    queryset = Testimonial.objects.all()
    serializer_class = TestimonialSerializer
    permission_classes = [AllowAny]

class NewsletterSignupView(generics.CreateAPIView):
    queryset = NewsletterSubscriber.objects.all()
    serializer_class = NewsletterSubscriberSerializer
    permission_classes = [AllowAny]