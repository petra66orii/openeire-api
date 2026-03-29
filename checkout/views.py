import stripe
import json
import logging
from datetime import timedelta
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from urllib.parse import urljoin
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.permissions import AllowAny, IsAuthenticated

from products.models import (
    Photo,
    Video,
    ProductVariant,
    PrintTemplate,
    LicenseRequest,
    LicenceOffer,
    StripeWebhookEvent,
)
from products.licensing import (
    ensure_licence_documents,
    ensure_delivery_token,
    send_licence_delivery_email,
    get_asset_file_field,
)
from products.personal_licence import get_personal_licence_summary, get_personal_licence_url
from userprofiles.models import UserProfile
from .models import Order, ProductShipping
from .serializers import OrderSerializer, OrderHistoryListSerializer
from .address_validation import validate_physical_shipping_address
from .prodigi import create_prodigi_order 

# Set the Stripe secret key
stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)

class CreatePaymentIntentView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        cart = request.data.get('cart')
        shipping_details = request.data.get('shipping_details')
        
        # NEW: Get the selected shipping method from the frontend (default to budget)
        shipping_method = request.data.get('shipping_method', 'budget')
        
        if cart is None:
            return Response({"error": "Cart is empty."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(cart, list):
            return Response(
                {"error": "Invalid cart payload. Expected a list of cart items."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(cart) == 0:
            return Response({"error": "Cart is empty."}, status=status.HTTP_400_BAD_REQUEST)
        if shipping_details is not None and not isinstance(shipping_details, dict):
            return Response(
                {
                    "shipping_details": {
                        "address": "Invalid shipping_details payload. Expected an object."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        total = 0
        shipping_cost = 0.00
        model_map = {'photo': Photo, 'video': Video, 'physical': ProductVariant}

        # Safely extract the country from shipping details (default to IE if missing)
        shipping_country = 'IE'
        address_payload_invalid = False
        if shipping_details and isinstance(shipping_details, dict):
            raw_address = shipping_details.get('address')
            if raw_address is None:
                address = {}
            elif isinstance(raw_address, dict):
                address = raw_address
                shipping_country = address.get('country', 'IE')
            else:
                address = {}
                address_payload_invalid = True
        else:
            address = {}

        has_physical_items = any(
            isinstance(item, dict) and item.get("product_type") == "physical"
            for item in cart
        )
        has_digital_items = any(
            isinstance(item, dict) and item.get("product_type") in {"photo", "video"}
            for item in cart
        )
        if has_digital_items and not request.user.is_authenticated:
            return Response(
                {
                    "code": "AUTH_REQUIRED_DIGITAL_CHECKOUT",
                    "error": "Authentication is required to purchase digital items.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if has_physical_items:
            if address_payload_invalid:
                return Response(
                    {"shipping_details": {"address": "Invalid address payload. Expected an object."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            shipping_errors = validate_physical_shipping_address(
                country=address.get("country"),
                line1=address.get("line1"),
                town=address.get("city"),
                postcode=address.get("postal_code"),
                county=address.get("state"),
            )
            if shipping_errors:
                field_map = {
                    "street_address1": "line1",
                    "town": "city",
                    "postcode": "postal_code",
                    "county": "state",
                    "country": "country",
                }
                shipping_errors = {
                    field_map.get(field, field): message
                    for field, message in shipping_errors.items()
                }
                return Response(
                    {"shipping_details": shipping_errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            shipping_country = str(address.get("country", "")).strip().upper()

        try:
            for item in cart:
                if not isinstance(item, dict):
                    return Response(
                        {
                            "code": "INVALID_CART_PAYLOAD",
                            "error": "Invalid cart item payload. Expected an object.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                product_id = item['product_id']
                product_type = item['product_type']
                quantity = item.get('quantity', 1)
                
                model_class = model_map.get(product_type)
                if not model_class: continue

                if product_type == 'physical':
                    product_instance = model_class.objects.get(
                        id=product_id,
                        photo__is_active=True,
                        photo__is_printable=True,
                    )
                else:
                    product_instance = model_class.objects.get(id=product_id)
                
                # Digital items use a single price; physical variants use their own price.
                if product_type in ['photo', 'video']:
                    options = item.get('options') or {}
                    if not isinstance(options, dict):
                        return Response(
                            {"error": f"Invalid options payload for digital item {product_id}."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    price_str = product_instance.price
                else:
                    price_str = getattr(product_instance, 'price', '0')
                    
                price = float(price_str)
                total += price * quantity

                # --- NEW DYNAMIC SHIPPING CALCULATION ---
                if product_type == 'physical':
                    try:
                        template = PrintTemplate.objects.get(
                            material=product_instance.material, 
                            size=product_instance.size
                        )
                        shipping_rule = ProductShipping.objects.get(
                            product=template, 
                            country=shipping_country, 
                            method=shipping_method
                        )
                        shipping_cost += float(shipping_rule.cost) * quantity
                    except (PrintTemplate.DoesNotExist, ProductShipping.DoesNotExist):
                        logger.warning(
                            "No shipping rule found for checkout item "
                            "(material=%s, size=%s, country=%s, method=%s)",
                            product_instance.material,
                            product_instance.size,
                            shipping_country,
                            shipping_method,
                        )
                        # You could add a fallback flat rate here if desired

        except (KeyError, TypeError, ValueError, ObjectDoesNotExist) as e:
            logger.warning(
                "CreatePaymentIntentView received invalid cart data "
                "(user_id=%s, authenticated=%s, error_type=%s)",
                request.user.id if request.user.is_authenticated else None,
                bool(request.user.is_authenticated),
                e.__class__.__name__,
            )
            return Response(
                {
                    "code": "INVALID_CART_PAYLOAD",
                    "error": "Invalid cart data provided.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Add dynamic shipping to total
        grand_total = total + shipping_cost
        amount_in_cents = int(grand_total * 100)

        customer_email = None
        if request.user.is_authenticated:
            customer_email = request.user.email
        elif isinstance(shipping_details, dict):
            customer_email = shipping_details.get('email')

        try:
            metadata = {
                'cart': json.dumps(cart),
                'username': request.user.username if request.user.is_authenticated else 'Guest',
                'user_id': str(request.user.id) if request.user.is_authenticated else '',
                'save_info': str(request.data.get('save_info', False)).lower(),
                'shipping_cost': shipping_cost,
                'shipping_method': shipping_method # Store method in metadata for Webhook
            }

            intent = stripe.PaymentIntent.create(
                amount=amount_in_cents,
                currency='eur',
                automatic_payment_methods={'enabled': True},
                metadata=metadata,
                receipt_email=customer_email 
            )
            
            return Response({
                'clientSecret': intent.client_secret,
                'shippingCost': shipping_cost, 
                'totalPrice': grand_total
            }, status=status.HTTP_200_OK)
        except Exception:
            logger.exception(
                "CreatePaymentIntentView failed while creating Stripe intent "
                "(user_id=%s, authenticated=%s, cart_items=%s, has_shipping=%s)",
                request.user.id if request.user.is_authenticated else None,
                bool(request.user.is_authenticated),
                len(cart),
                bool(shipping_details),
            )
            return Response(
                {
                    "code": "PAYMENT_INTENT_CREATION_FAILED",
                    "error": "Unable to initialize checkout right now. Please try again.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class StripeWebhookView(APIView):
    authentication_classes = [] 
    permission_classes = [AllowAny]
    SUPPORTED_EVENT_TYPES = {'payment_intent.succeeded', 'checkout.session.completed'}

    def _stale_processing_seconds(self):
        return int(getattr(settings, "STRIPE_WEBHOOK_STALE_PROCESSING_SECONDS", 600))

    def _allow_legacy_username_fallback(self):
        return bool(getattr(settings, "CHECKOUT_ALLOW_LEGACY_USERNAME_FALLBACK", False))

    def _summarize_validation_errors(self, errors):
        if hasattr(errors, "keys"):
            fields = sorted(str(field) for field in errors.keys())
            return ", ".join(fields) if fields else "unknown"
        return "unknown"

    def _send_confirmation_email(self, order, request=None):
        """Send the user a confirmation email"""
        cust_email = order.email
        context = {
            'order': order,
            'contact_email': settings.DEFAULT_FROM_EMAIL,
            'personal_terms_url': get_personal_licence_url(request=request),
            'personal_terms_summary': get_personal_licence_summary(),
        }
        subject = render_to_string(
            'checkout/confirmation_emails/confirmation_email_subject.txt',
            context,
        )
        body = render_to_string(
            'checkout/confirmation_emails/confirmation_email_body.txt',
            context,
        )
        
        email = EmailMessage(
            subject=subject.strip(),
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[cust_email],
        )
        email.send(fail_silently=False)

    def _extract_payment_link_id(self, session):
        return session.get('payment_link') or session.get('metadata', {}).get('payment_link_id')

    def _build_license_download_url(self, request, token_obj):
        path = reverse('license-asset-download', args=[str(token_obj.token)])
        base_url = getattr(settings, "LICENCE_DOWNLOAD_BASE_URL", None)
        if base_url:
            return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        return request.build_absolute_uri(path)

    def _handle_license_payment(self, request, session):
        payment_link_id = self._extract_payment_link_id(session)
        payment_status = session.get('payment_status')
        checkout_session_id = session.get('id')
        payment_intent_id = session.get('payment_intent')

        if not payment_link_id:
            return

        if payment_status != 'paid':
            logger.warning(
                "Payment link session not paid yet (status=%s). Skipping approval.",
                payment_status,
            )
            return

        offer = (
            LicenceOffer.objects
            .filter(stripe_payment_link_id=payment_link_id)
            .select_related('license_request')
            .order_by('-version')
            .first()
        )

        if offer:
            license_request = offer.license_request
        else:
            matching_requests = LicenseRequest.objects.filter(
                stripe_payment_link_id=payment_link_id
            )

            if matching_requests.count() != 1:
                # Fallback for legacy rows that only store the URL
                try:
                    payment_link = stripe.PaymentLink.retrieve(payment_link_id)
                    link_url = getattr(payment_link, "url", None)
                except Exception:
                    logger.exception(
                        "Could not retrieve payment link for id=%s.",
                        payment_link_id,
                    )
                    link_url = None

                if link_url:
                    matching_requests = LicenseRequest.objects.filter(
                        stripe_payment_link=link_url
                    )

                if matching_requests.count() != 1:
                    logger.warning(
                        "Expected exactly one LicenseRequest for payment_link_id=%s, found=%s.",
                        payment_link_id,
                        matching_requests.count(),
                    )
                    return
            license_request = matching_requests.first()

        if not license_request.stripe_payment_link_id:
            license_request.stripe_payment_link_id = payment_link_id
            license_request.save(update_fields=['stripe_payment_link_id', 'updated_at'])

        if license_request.status == 'DELIVERED':
            logger.info(
                "License request already delivered; skipping. license_request_id=%s",
                license_request.id,
            )
            return

        asset = license_request.asset
        file_field = get_asset_file_field(asset)
        if not file_field:
            raise RuntimeError(f"No high-res file found attached to asset {asset}")

        issued_at = timezone.now()

        if offer and offer.status != 'PAID':
            offer.status = 'PAID'
            offer.paid_at = issued_at
            offer.stripe_checkout_session_id = checkout_session_id
            offer.stripe_payment_intent_id = payment_intent_id
            offer.save(
                update_fields=[
                    'status',
                    'paid_at',
                    'stripe_checkout_session_id',
                    'stripe_payment_intent_id',
                ]
            )

        license_request.stripe_checkout_session_id = checkout_session_id
        license_request.stripe_payment_intent_id = payment_intent_id
        if not license_request.paid_at:
            license_request.paid_at = issued_at
        license_request.save(
            update_fields=[
                'stripe_checkout_session_id',
                'stripe_payment_intent_id',
                'paid_at',
                'updated_at',
            ]
        )

        if license_request.status != 'PAID':
            license_request.transition_to(
                'PAID',
                note="Stripe checkout.session.completed received.",
                metadata={
                    "checkout_session_id": checkout_session_id,
                    "payment_intent_id": payment_intent_id,
                    "payment_link_id": payment_link_id,
                },
            )

        terms_version = offer.terms_version if offer else None
        documents = ensure_licence_documents(
            license_request,
            issued_at=issued_at,
            terms_version=terms_version,
        )
        token_obj = ensure_delivery_token(license_request)
        download_url = self._build_license_download_url(request, token_obj)

        send_licence_delivery_email(license_request, documents, download_url, token_obj)

        license_request.delivered_at = timezone.now()
        license_request.save(update_fields=['delivered_at', 'updated_at'])
        license_request.transition_to(
            'DELIVERED',
            note="Licence documents generated and delivery email sent.",
            metadata={"checkout_session_id": checkout_session_id},
        )

        logger.info(
            "Rights-managed license delivered successfully. license_request_id=%s",
            license_request.id,
        )

    def post(self, request):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        webhook_secret = settings.STRIPE_WEBHOOK_SECRET
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError):
            logger.warning("Webhook signature verification failed.", exc_info=True)
            return Response(status=status.HTTP_400_BAD_REQUEST)

        event_id = event.get('id')
        event_type = event.get('type')
        logger.info("Stripe webhook received. event_type=%s event_id=%s", event_type, event_id)
        if event_type not in self.SUPPORTED_EVENT_TYPES:
            return Response(status=status.HTTP_200_OK)

        should_process_event = True
        event_record = None
        if not event_id:
            logger.warning("Stripe event missing id; skipping idempotency tracking.")
        else:
            with transaction.atomic():
                event_record, created = StripeWebhookEvent.objects.get_or_create(
                    stripe_event_id=event_id,
                    defaults={
                        'event_type': event_type or 'unknown',
                        'status': 'PROCESSING',
                    }
                )
                event_record = StripeWebhookEvent.objects.select_for_update().get(pk=event_record.pk)
                if not created:
                    if event_record.status == 'SUCCESS':
                        logger.info("Stripe event already processed; skipping. event_id=%s", event_id)
                        should_process_event = False
                    elif event_record.status == 'PROCESSING':
                        stale_before = timezone.now() - timedelta(
                            seconds=self._stale_processing_seconds()
                        )
                        is_stale = (
                            event_record.processed_at is None
                            and event_record.received_at is not None
                            and event_record.received_at <= stale_before
                        )
                        if is_stale:
                            logger.warning(
                                "Stripe event had stale PROCESSING state; retrying. event_id=%s",
                                event_id,
                            )
                            event_record.error_message = "Recovered from stale PROCESSING state."
                            event_record.event_type = event_type or event_record.event_type or 'unknown'
                            event_record.save(update_fields=['error_message', 'event_type'])
                        else:
                            logger.info(
                                "Stripe event is already being processed; skipping. event_id=%s",
                                event_id,
                            )
                            should_process_event = False
                    else:
                        logger.info("Retrying failed Stripe event. event_id=%s", event_id)
                        event_record.status = 'PROCESSING'
                        event_record.error_message = None
                        event_record.processed_at = None
                        event_record.event_type = event_type or event_record.event_type or 'unknown'
                        event_record.save(update_fields=['status', 'error_message', 'processed_at', 'event_type'])
        if not should_process_event:
            return Response(status=status.HTTP_200_OK)

        processing_error = None

        try:
            if event_type == 'payment_intent.succeeded':
                payment_intent = event['data']['object']

                # 1. Get all data from Stripe
                metadata = payment_intent.get('metadata', {})
                cart_items_str = metadata.get('cart', '[]')
                shipping_details = payment_intent.get('shipping') or {}
                address_details = shipping_details.get('address') or {}

                shipping_cost = float(metadata.get('shipping_cost', 0.00))
                shipping_method = metadata.get('shipping_method', 'budget')

                # Skip order creation for non-cart flows (e.g., Payment Links)
                try:
                    cart_items = json.loads(cart_items_str)
                except (TypeError, ValueError):
                    cart_items = []

                if not cart_items:
                    logger.info("No cart items found for payment_intent; skipping order creation.")
                    return Response(status=status.HTTP_200_OK)

                # 2. Determine the email
                order_email = payment_intent.get('receipt_email')
                if not order_email or '@' not in order_email:
                    order_email = metadata.get('username')
                    if not order_email or '@' not in order_email:
                        order_email = "guest@example.com"

                # 3. Create the order_data dictionary
                order_data = {
                    'stripe_pid': payment_intent.get('id', ''),
                    'first_name': shipping_details.get('name', ''),
                    'email': order_email,
                    'phone_number': shipping_details.get('phone', ''),
                    'country': address_details.get('country', ''),
                    'town': address_details.get('city', ''),
                    'street_address1': address_details.get('line1', ''),
                    'street_address2': address_details.get('line2', ''),
                    'postcode': address_details.get('postal_code', ''),
                    'county': address_details.get('state', ''),
                    'items': cart_items,
                    'delivery_cost': shipping_cost,
                    'shipping_method': shipping_method, # NEW: Pass this to the serializer
                }

                # 4. Check for User Profile
                user_id = metadata.get('user_id')
                save_info = metadata.get('save_info') == 'true'
                profile = None

                if user_id:
                    try:
                        profile = UserProfile.objects.get(user__id=int(user_id))
                    except (TypeError, ValueError, UserProfile.DoesNotExist):
                        profile = None

                if profile is None and self._allow_legacy_username_fallback():
                    username = metadata.get('username')
                    if username and username != 'Guest':
                        try:
                            profile = UserProfile.objects.get(user__username=username)
                            logger.warning(
                                "Using legacy username fallback for webhook order binding. "
                                "Disable CHECKOUT_ALLOW_LEGACY_USERNAME_FALLBACK once old payment intents are drained."
                            )
                        except UserProfile.DoesNotExist:
                            profile = None

                if profile is not None:
                    order_data['user_profile'] = profile.id
                    order_data['email'] = profile.user.email

                    if save_info:
                        profile.default_phone_number = shipping_details.get('phone', profile.default_phone_number)
                        profile.default_street_address1 = address_details.get('line1', profile.default_street_address1)
                        profile.default_street_address2 = address_details.get('line2', profile.default_street_address2)
                        profile.default_town = address_details.get('city', profile.default_town)
                        profile.default_postcode = address_details.get('postal_code', profile.default_postcode)
                        profile.default_county = address_details.get('state', profile.default_county)
                        profile.default_country = address_details.get('country', profile.default_country)
                        profile.save()

                # 5. Validate and Save
                serializer = OrderSerializer(data=order_data)
                if serializer.is_valid():
                    order = serializer.save()
                    logger.info("Order created successfully. order_number=%s", order.order_number)

                    try:
                        self._send_confirmation_email(order, request=request)
                        logger.info("Confirmation email sent. order_number=%s", order.order_number)
                    except Exception:
                        logger.exception(
                            "Could not send confirmation email. order_number=%s",
                            order.order_number,
                        )
                    
                    try:
                        has_physical_items = False
                        for item in order.items.all():
                            if item.content_type.model == 'productvariant':
                                has_physical_items = True
                                break
                        
                        if has_physical_items:
                            # Only contact Prodigi if we actually have prints
                            logger.info("Sending order to Prodigi. order_number=%s", order.order_number)
                            create_prodigi_order(order)
                            logger.info("Order sent to Prodigi successfully. order_number=%s", order.order_number)
                        else:
                            # Digital-only order: Stay silent
                            logger.info(
                                "Digital-only order detected; skipping Prodigi fulfillment. order_number=%s",
                                order.order_number,
                            )

                    except Exception:
                        logger.exception(
                            "Failed to fulfill order after webhook processing. order_number=%s",
                            order.order_number,
                        )

                else:
                    error_fields = self._summarize_validation_errors(serializer.errors)
                    logger.error("Error creating order. Validation fields: %s", error_fields)
                    processing_error = f"Order validation failed. Fields: {error_fields}"
                    logger.warning(
                        "Acknowledging webhook event despite validation failure. %s",
                        processing_error,
                    )

            elif event_type == 'checkout.session.completed':
                session = event['data']['object']
                self._handle_license_payment(request, session)
        except Exception as e:
            processing_error = str(e)
            logger.exception("Error processing Stripe event. event_id=%s", event_id)
        finally:
            if event_record and should_process_event:
                StripeWebhookEvent.objects.filter(pk=event_record.pk).update(
                    status='FAILED' if processing_error else 'SUCCESS',
                    processed_at=timezone.now(),
                    error_message=processing_error,
                    event_type=event_type or 'unknown',
                )
        
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
