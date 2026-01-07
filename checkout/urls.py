from django.urls import path
from .views import CreatePaymentIntentView, StripeWebhookView, OrderHistoryView

urlpatterns = [
    path('create-payment-intent/', CreatePaymentIntentView.as_view(), name='create_payment_intent'),
    path('wh/', StripeWebhookView.as_view(), name='webhook'),
    path('order-history/', OrderHistoryView.as_view(), name='order_history'),
]