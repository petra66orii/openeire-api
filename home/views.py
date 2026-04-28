from rest_framework import generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.core.mail import send_mail
from django.conf import settings
from rest_framework.permissions import AllowAny
from openeire_api.mail_utils import get_contact_email_address, get_default_from_email
from .models import Testimonial, NewsletterSubscriber
from .serializers import TestimonialSerializer, NewsletterSubscriberSerializer, ContactFormSerializer

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

class ContactFormView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        serializer = ContactFormSerializer(data=request.data)
        if serializer.is_valid():
            # Extract validated data
            data = serializer.validated_data
            name = data['name']
            email = data['email']
            subject = data['subject']
            message = data['message']

            # Construct the email body
            email_body = (
                f"New Message from OpenÉire Studios Contact Form\n\n"
                f"From: {name} ({email})\n"
                f"Subject: {subject}\n\n"
                f"Message:\n{message}"
            )

            try:
                # Send email to Admin
                # Ensure DEFAULT_FROM_EMAIL and ADMIN_EMAIL are set in settings.py
                send_mail(
                    subject=f"Contact Form: {subject}",
                    message=email_body,
                    from_email=get_default_from_email(),
                    recipient_list=[get_contact_email_address()], # Or a specific admin email
                    fail_silently=False,
                )
                return Response(
                    {"message": "Email sent successfully"}, 
                    status=status.HTTP_200_OK
                )
            except Exception as e:
                # Log the error in a real app
                return Response(
                    {"error": "Failed to send email. Please try again later."}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
