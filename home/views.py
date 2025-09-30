from rest_framework import generics
from rest_framework.permissions import AllowAny
from .models import Testimonial
from .serializers import TestimonialSerializer

class TestimonialListView(generics.ListAPIView):
    """
    API endpoint to list all testimonials.
    """
    queryset = Testimonial.objects.all()
    serializer_class = TestimonialSerializer
    permission_classes = [AllowAny]