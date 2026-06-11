from django.urls import path
from .views import CreatePaymentIntentView, DiscountValidationView, StripeWebhookView, OrderHistoryView, ProdigiCallbackView

urlpatterns = [
    path('validate-discount/', DiscountValidationView.as_view(), name='validate_discount'),
    path('create-payment-intent/', CreatePaymentIntentView.as_view(), name='create_payment_intent'),
    path('wh/', StripeWebhookView.as_view(), name='webhook'),
    path('order-history/', OrderHistoryView.as_view(), name='order_history'),
    path('prodigi/callback/', ProdigiCallbackView.as_view(), name='prodigi_callback'),
]
