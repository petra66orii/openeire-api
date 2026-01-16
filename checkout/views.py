import stripe
import json
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.permissions import AllowAny, IsAuthenticated
from products.models import Photo, Video, ProductVariant
from userprofiles.models import UserProfile
from .models import Order
from .serializers import OrderSerializer, OrderHistoryListSerializer
from .prodigi import create_prodigi_order 

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
        model_map = {'photo': Photo, 'video': Video, 'physical': ProductVariant}
        has_physical_items = False

        try:
            for item in cart:
                product_id = item['product_id']
                product_type = item['product_type']
                quantity = item['quantity']
                if product_type == 'physical':
                    has_physical_items = True
                
                model_class = model_map.get(product_type)
                if not model_class: continue

                product_instance = model_class.objects.get(id=product_id)
                price_str = getattr(product_instance, 'price', getattr(product_instance, 'price_hd', '0'))
                price = float(price_str)
                total += price * quantity

        except (KeyError, model_class.DoesNotExist) as e:
            return Response({"error": f"Invalid cart data provided: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        
        shipping_cost = 5.99 if has_physical_items else 0.00
        
        # Add shipping to total
        grand_total = total + shipping_cost
        amount_in_cents = int(grand_total * 100)

        customer_email = None
        if shipping_details:
            customer_email = shipping_details.get('email')
        
        if not customer_email and request.user.is_authenticated:
             customer_email = request.user.email

        try:
            metadata = {
                'cart': json.dumps(cart),
                'username': request.user.username if request.user.is_authenticated else 'Guest',
                'save_info': str(request.data.get('save_info', False)).lower(),
                'shipping_cost': shipping_cost
            }

            intent = stripe.PaymentIntent.create(
                amount=amount_in_cents,
                currency='eur',
                automatic_payment_methods={'enabled': True},
                metadata=metadata,
                receipt_email=customer_email 
            )
            
            return Response({'clientSecret': intent.client_secret}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class StripeWebhookView(APIView):
    permission_classes = [AllowAny]

    def _send_confirmation_email(self, order):
        """Send the user a confirmation email"""
        cust_email = order.email
        subject = render_to_string(
            'checkout/confirmation_emails/confirmation_email_subject.txt',
            {'order': order}
        )
        body = render_to_string(
            'checkout/confirmation_emails/confirmation_email_body.txt',
            {'order': order, 'contact_email': settings.DEFAULT_FROM_EMAIL}
        )
        
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [cust_email]
        )

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

            shipping_cost = float(metadata.get('shipping_cost', 0.00))

            # 2. Determine the email
            order_email = payment_intent.receipt_email
            if not order_email or '@' not in order_email:
                order_email = metadata.get('username')
                if not order_email or '@' not in order_email:
                    order_email = "guest@example.com"

            # 3. Create the order_data dictionary
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
                'delivery_cost': shipping_cost,
            }

            # 4. Check for User Profile
            username = metadata.get('username')
            save_info = metadata.get('save_info') == 'true'

            if username and username != 'Guest':
                try:
                    profile = UserProfile.objects.get(user__username=username)
                    order_data['user_profile'] = profile.id
                    
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

            # 5. Validate and Save
            serializer = OrderSerializer(data=order_data)
            if serializer.is_valid():
                order = serializer.save()
                print(f"✅ Order {order.order_number} created successfully. Email: {order.email}")

                try:
                    self._send_confirmation_email(order)
                    print(f"📧 Confirmation email sent to {order.email}")
                except Exception as e:
                    print(f"❌ EMAIL ERROR: Could not send email to {order.email}: {e}")
                
                try:
                    has_physical_items = False
                    for item in order.items.all():
                        if item.content_type.model == 'productvariant':
                            has_physical_items = True
                            break
                    
                    if has_physical_items:
                        # Only contact Prodigi if we actually have prints
                        print(f"🏭 Sending Order {order.order_number} to Prodigi...")
                        create_prodigi_order(order)
                        print(f"🚀 Sent to Prodigi successfully!")
                    else:
                        # Digital-only order: Stay silent
                        print(f"💾 Digital Order {order.order_number} detected. Skipping Prodigi fulfillment.")

                except Exception as e:
                    print(f"❌ PRODIGI ERROR: Failed to fulfill order {order.order_number}: {e}")

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