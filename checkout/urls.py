from django.urls import path
from .views import CreatePaymentIntentView, StripeWebhookView, OrderHistoryView, ProdigiCallbackView

urlpatterns = [
    path('create-payment-intent/', CreatePaymentIntentView.as_view(), name='create_payment_intent'),
    path('wh/', StripeWebhookView.as_view(), name='webhook'),
    path('order-history/', OrderHistoryView.as_view(), name='order_history'),
    path('prodigi/callback/', ProdigiCallbackView.as_view(), name='prodigi_callback'),
]
