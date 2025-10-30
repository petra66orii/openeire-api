from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from products.models import Photo, Video, Product
from userprofiles.models import UserProfile
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from .models import Order
import stripe
import json

from .serializers import OrderSerializer, OrderHistoryListSerializer

# Set the Stripe secret key
stripe.api_key = settings.STRIPE_SECRET_KEY

class CreatePaymentIntentView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        cart = request.data.get('cart')
        shipping_details = request.data.get('shipping_details')
        
        if not cart:
            return Response({"error": "Cart is empty."}, status=status.HTTP_400_BAD_REQUEST)
        
        total = 0
        model_map = {'photo': Photo, 'video': Video, 'physical': Product}

        try:
            for item in cart:
                product_id = item['product_id']
                product_type = item['product_type']
                quantity = item['quantity']
                
                model_class = model_map.get(product_type)
                if not model_class: continue

                product_instance = model_class.objects.get(id=product_id)
                price_str = getattr(product_instance, 'price', getattr(product_instance, 'price_hd', '0'))
                price = float(price_str)
                total += price * quantity

        except (KeyError, model_class.DoesNotExist) as e:
            return Response({"error": f"Invalid cart data provided: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        
        amount_in_cents = int(total * 100)

        customer_email = None
        if shipping_details:
            customer_email = shipping_details.get('email')
        
        # Fallback if no email (e.g., guest just loaded)
        if not customer_email and request.user.is_authenticated:
             customer_email = request.user.email

        try:
            metadata = {
                'cart': json.dumps(cart), # The cart is already the simplified version
                'username': request.user.username if request.user.is_authenticated else 'Guest'
            }

            intent = stripe.PaymentIntent.create(
                amount=amount_in_cents,
                currency='eur',
                automatic_payment_methods={'enabled': True},
                metadata=metadata
            )
            
            return Response({'clientSecret': intent.client_secret}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class StripeWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        webhook_secret = settings.STRIPE_WEBHOOK_SECRET
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            print(f"Webhook signature verification failed: {e}")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if event['type'] == 'payment_intent.succeeded':
            payment_intent = event['data']['object']
            
            # 1. Get all data from Stripe
            metadata = payment_intent.get('metadata', {})
            cart_items_str = metadata.get('cart', '[]')
            shipping_details = payment_intent.get('shipping') or {}
            address_details = shipping_details.get('address') or {}

            # 2. Determine the email
            order_email = payment_intent.receipt_email
            if not order_email or not '@' in order_email:
                order_email = metadata.get('username')
                if not order_email or not '@' in order_email:
                    order_email = "guest@example.com"

            # 3. Create the order_data dictionary FIRST
            order_data = {
                'stripe_pid': payment_intent.id,
                'first_name': shipping_details.get('name', ''),
                'email': order_email,
                'phone_number': shipping_details.get('phone', ''),
                'country': address_details.get('country', ''),
                'town': address_details.get('city', ''),
                'street_address1': address_details.get('line1', ''),
                'street_address2': address_details.get('line2', ''),
                'postcode': address_details.get('postal_code', ''),
                'county': address_details.get('state', ''),
                'items': json.loads(cart_items_str),
            }

            # 4. NOW, check for the logged-in user and modify the dictionary
            username = metadata.get('username')
            save_info = metadata.get('save_info') == 'true'

            if username and username != 'Guest':
                try:
                    profile = UserProfile.objects.get(user__username=username)
                    order_data['user_profile'] = profile.id # <-- This is now safe
                    
                    if save_info:
                        profile.default_phone_number = shipping_details.get('phone', profile.default_phone_number)
                        profile.default_street_address1 = address_details.get('line1', profile.default_street_address1)
                        profile.default_street_address2 = address_details.get('line2', profile.default_street_address2)
                        profile.default_town = address_details.get('city', profile.default_town)
                        profile.default_postcode = address_details.get('postal_code', profile.default_postcode)
                        profile.default_county = address_details.get('state', profile.default_county)
                        profile.default_country = address_details.get('country', profile.default_country)
                        profile.save()

                except UserProfile.DoesNotExist:
                    pass

            # 5. Validate and save
            serializer = OrderSerializer(data=order_data)
            if serializer.is_valid():
                order = serializer.save()
                print(f"✅ Order {order.order_number} created successfully. Email: {order.email}")
            else:
                print(f"❌ Error creating order: {serializer.errors}")
                return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(status=status.HTTP_200_OK)
        stripe.api_key = settings.STRIPE_SECRET_KEY
        webhook_secret = settings.STRIPE_WEBHOOK_SECRET
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            print(f"Webhook signature verification failed: {e}")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if event['type'] == 'payment_intent.succeeded':
            payment_intent = event['data']['object']
            metadata = payment_intent.get('metadata', {})
            
            metadata = payment_intent.get('metadata', {})
            cart_items_str = metadata.get('cart', '[]')
            
            shipping_details = payment_intent.get('shipping') or {}
            address_details = shipping_details.get('address') or {}

            # Try to get email from payment_intent.receipt_email, then metadata, then provide a placeholder
            order_email = payment_intent.receipt_email # This might be None for test cards
            if not order_email or not '@' in order_email: # Basic check for validity
                order_email = metadata.get('username') # If logged in
                if not order_email or not '@' in order_email:
                    order_email = "guest@example.com" # Fallback for guest or invalid test data

            username = metadata.get('username')
            save_info = metadata.get('save_info') == 'true'

            if username and username != 'Guest':
                try:
                    profile = UserProfile.objects.get(user__username=username)
                    order_data['user_profile'] = profile.id
                    
                    # If user checked "Save Info", update their profile
                    if save_info:
                        profile.default_phone_number = shipping_details.get('phone', profile.default_phone_number)
                        profile.default_street_address1 = address_details.get('line1', profile.default_street_address1)
                        profile.default_street_address2 = address_details.get('line2', profile.default_street_address2)
                        profile.default_town = address_details.get('city', profile.default_town)
                        profile.default_postcode = address_details.get('postal_code', profile.default_postcode)
                        profile.default_county = address_details.get('state', profile.default_county)
                        profile.default_country = address_details.get('country', profile.default_country)
                        profile.save()

                except UserProfile.DoesNotExist:
                    pass

            order_data = {
                'stripe_pid': payment_intent.id,
                'first_name': shipping_details.get('name', ''),
                'email': order_email,
                'phone_number': shipping_details.get('phone', ''),
                'country': address_details.get('country', ''),
                'town': address_details.get('city', ''),
                'street_address1': address_details.get('line1', ''),
                'street_address2': address_details.get('line2', ''),
                'postcode': address_details.get('postal_code', ''),
                'county': address_details.get('state', ''),
                'items': json.loads(cart_items_str),
            }

            username = metadata.get('username')
            if username and username != 'Guest':
                try:
                    profile = UserProfile.objects.get(user__username=username)
                    order_data['user_profile'] = profile.id
                except UserProfile.DoesNotExist:
                    pass

            serializer = OrderSerializer(data=order_data)
            if serializer.is_valid():
                order = serializer.save()
                print(f"✅ Order {order.order_number} created successfully. Email: {order.email}")
            else:
                print(f"❌ Error creating order: {serializer.errors}")
                # Return the errors so we can see them more clearly if needed
                return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(status=status.HTTP_200_OK)

class OrderHistoryView(generics.ListAPIView):
    """
    API endpoint to list all orders for the currently authenticated user.
    """
    serializer_class = OrderHistoryListSerializer
    permission_classes = [IsAuthenticated] # Only logged-in users can see this

    def get_queryset(self):
        """
        This view should return a list of all the orders
        for the currently authenticated user.
        """
        return Order.objects.filter(user_profile=self.request.user.userprofile).order_by('-date')