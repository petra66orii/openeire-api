# checkout/urls.py
from django.urls import path
from .views import CreatePaymentIntentView, StripeWebhookView

urlpatterns = [
    path('create-payment-intent/', CreatePaymentIntentView.as_view(), name='create_payment_intent'),
    path('webhook/', StripeWebhookView.as_view(), name='stripe_webhook'),
]