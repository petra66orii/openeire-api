import stripe
import json
import logging
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from urllib.parse import urljoin
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated

from products.models import (
    Photo,
    Video,
    ProductVariant,
    LicenseRequest,
    LicenceOffer,
    StripeWebhookEvent,
)
from products.licensing import (
    ensure_licence_documents,
    ensure_delivery_token,
    send_licence_delivery_email,
)
from products.file_access import asset_file_exists, get_asset_file_name, open_asset_file
from products.personal_downloads import ensure_personal_download_token
from products.personal_licence import get_personal_terms_version
from realestate.models import RealEstateEnquiry
from realestate.payments import calculate_realestate_deposit_amounts
from userprofiles.models import UserProfile
from .attempts import (
    build_request_fingerprint,
    canonicalize_cart,
    canonicalize_shipping_details,
    normalize_checkout_key,
)
from .models import CheckoutAttempt, Order
from .serializers import OrderSerializer, OrderHistoryListSerializer
from .discounts import (
    DiscountRedemptionConflict,
    evaluate_discount,
    normalize_discount_code,
    record_discount_redemption,
)
from .address_validation import validate_physical_shipping_address
from .prodigi import create_prodigi_order, fetch_prodigi_order
from .alerts import send_fulfilment_failure_alert
from .order_claiming import claim_guest_orders_for_user
from .emails import send_order_confirmation_email
from .shipping import (
    ShippingConfigurationError,
    calculate_physical_shipping_quote,
    get_free_shipping_threshold,
)
from .tracking import (
    sync_order_shipping_from_prodigi,
)
from openeire_api.throttling import SharedScopedRateThrottle

# Set the Stripe secret key
stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)


def _validated_email_or_none(value):
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        validate_email(candidate)
    except DjangoValidationError:
        return None
    return candidate.lower()

def _configured_stripe_payment_method_types():
    configured = getattr(settings, "STRIPE_PAYMENT_METHOD_TYPES", None)
    if isinstance(configured, (list, tuple)):
        cleaned = [
            value.strip()
            for value in configured
            if isinstance(value, str) and value.strip()
        ]
        if cleaned:
            return cleaned
    return ["card"]


def _safe_decimal_from_metadata(value, *, field_name, event_id=None):
    raw_value = str(value or "").strip()
    if not raw_value:
        return Decimal("0")
    try:
        return Decimal(raw_value)
    except Exception:
        logger.warning(
            "Stripe metadata field %s was not a valid decimal; defaulting to 0. event_id=%s raw_value=%r",
            field_name,
            event_id or "unknown",
            raw_value,
        )
        return Decimal("0")


def _extract_checkout_customer_email(*, request, shipping_details):
    if request.user.is_authenticated:
        return _validated_email_or_none(request.user.email)
    if isinstance(shipping_details, dict):
        return _validated_email_or_none(shipping_details.get("email"))
    return None


def _validated_cart_quantity(value):
    if isinstance(value, bool):
        raise DRFValidationError(
            {
                "code": "INVALID_CART_PAYLOAD",
                "error": "Cart quantity must be a whole number of at least 1.",
            }
        )
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise DRFValidationError(
            {
                "code": "INVALID_CART_PAYLOAD",
                "error": "Cart quantity must be a whole number of at least 1.",
            }
        )
    if quantity < 1:
        raise DRFValidationError(
            {
                "code": "INVALID_CART_PAYLOAD",
                "error": "Cart quantity must be a whole number of at least 1.",
            }
        )
    return quantity


def _resolve_cart_pricing(cart):
    total = Decimal("0.00")
    eligible_physical_subtotal = Decimal("0.00")
    model_map = {'photo': Photo, 'video': Video, 'physical': ProductVariant}
    physical_line_items = []
    pricing_snapshot = []

    for item in cart:
        if not isinstance(item, dict):
            raise DRFValidationError(
                {
                    "code": "INVALID_CART_PAYLOAD",
                    "error": "Invalid cart item payload. Expected an object.",
                }
            )

        product_id = item['product_id']
        product_type = item['product_type']
        quantity = _validated_cart_quantity(item.get('quantity', 1))

        model_class = model_map.get(product_type)
        if not model_class:
            continue

        if product_type == 'physical':
            product_instance = model_class.objects.get(
                id=product_id,
                photo__is_active=True,
                photo__is_printable=True,
            )
        elif product_type == 'photo':
            product_instance = model_class.objects.get(id=product_id, is_active=True)
        elif product_type == 'video':
            product_instance = model_class.objects.get(id=product_id, is_active=True)
        else:
            product_instance = model_class.objects.get(id=product_id)

        if product_type in ['photo', 'video']:
            if not asset_file_exists(product_instance):
                raise DRFValidationError(
                    {
                        "code": "DIGITAL_ASSET_UNAVAILABLE",
                        "error": f"Digital product {product_id} is unavailable for delivery.",
                    }
                )
            options = item.get('options') or {}
            if not isinstance(options, dict):
                raise DRFValidationError(
                    {"error": f"Invalid options payload for digital item {product_id}."}
                )
            price_str = product_instance.price
        else:
            price_str = getattr(product_instance, 'price', '0')

        price = Decimal(str(price_str))
        line_total = price * quantity
        total += line_total
        pricing_snapshot.append(
            {
                "product_id": product_id,
                "product_type": product_type,
                "quantity": quantity,
                "unit_price": str(price),
                "item_total": str(line_total),
            }
        )

        if product_type == 'physical':
            eligible_physical_subtotal += line_total
            physical_line_items.append((product_instance, quantity))

    return {
        "cart_total": total,
        "eligible_physical_subtotal": eligible_physical_subtotal,
        "physical_line_items": physical_line_items,
        "pricing_snapshot": pricing_snapshot,
    }


def _validate_checkout_attempt_pricing(attempt):
    cart = attempt.cart_snapshot
    pricing = attempt.pricing_snapshot
    if not isinstance(cart, list) or not isinstance(pricing, list) or len(cart) != len(pricing):
        raise DRFValidationError(
            {"payment": "The payment-time pricing snapshot is incomplete."}
        )

    subtotal = Decimal("0.00")
    try:
        for cart_item, price_item in zip(cart, pricing):
            if (
                int(cart_item["product_id"]) != int(price_item["product_id"])
                or cart_item["product_type"] != price_item["product_type"]
                or int(cart_item["quantity"]) != int(price_item["quantity"])
            ):
                raise ValueError("Pricing snapshot item mismatch")
            quantity = int(price_item["quantity"])
            unit_price = Decimal(str(price_item["unit_price"]))
            item_total = Decimal(str(price_item["item_total"]))
            if quantity < 1 or unit_price < 0 or item_total != unit_price * quantity:
                raise ValueError("Invalid pricing snapshot amount")
            subtotal += item_total
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise DRFValidationError(
            {"payment": "The payment-time pricing snapshot is invalid."}
        )

    charged_total = subtotal + attempt.shipping_cost - attempt.discount_amount
    amount_in_cents = int(
        (charged_total * Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    if charged_total < 0 or amount_in_cents != attempt.expected_amount_cents:
        raise DRFValidationError(
            {"payment": "The payment-time pricing snapshot does not match the charge."}
        )


class CreatePaymentIntentView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = "checkout_payment_intent"

    @staticmethod
    def _intent_value(intent, key, default=None):
        if isinstance(intent, dict):
            return intent.get(key, default)
        return getattr(intent, key, default)

    def _response_for_attempt(self, attempt, intent):
        client_secret = self._intent_value(intent, "client_secret")
        if not isinstance(client_secret, str) or not client_secret:
            raise RuntimeError("Stripe did not return a PaymentIntent client secret.")
        return Response(
            {
                "clientSecret": client_secret,
                "paymentIntentId": attempt.payment_intent_id,
                "shippingCost": float(attempt.shipping_cost),
                "discountAmount": float(attempt.discount_amount),
                "discountCode": attempt.discount_code,
                "discountLabel": attempt.discount_label,
                "totalPrice": float(Decimal(attempt.expected_amount_cents) / Decimal("100")),
                "freeShippingApplied": attempt.free_shipping_applied,
                "freeShippingThreshold": float(get_free_shipping_threshold()),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, *args, **kwargs):
        raw_cart = request.data.get("cart")
        if raw_cart is None or raw_cart == []:
            return Response({"error": "Cart is empty."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(raw_cart, list):
            return Response(
                {"error": "Invalid cart payload. Expected a list of cart items."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw_shipping_details = request.data.get("shipping_details")
        if raw_shipping_details is not None and not isinstance(raw_shipping_details, dict):
            return Response(
                {
                    "shipping_details": {
                        "address": "Invalid shipping_details payload. Expected an object."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            isinstance(raw_shipping_details, dict)
            and raw_shipping_details.get("address") is not None
            and not isinstance(raw_shipping_details.get("address"), dict)
        ):
            return Response(
                {"shipping_details": {"address": "Invalid address payload. Expected an object."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            checkout_key = normalize_checkout_key(request.data.get("checkout_id"))
            cart = canonicalize_cart(raw_cart)
        except DRFValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        shipping_details = canonicalize_shipping_details(raw_shipping_details)
        shipping_method = str(request.data.get("shipping_method") or "budget").strip().lower()
        address = shipping_details.get("address", {})
        shipping_country = address.get("country") or "IE"

        has_physical_items = any(
            item.get("product_type") == "physical"
            for item in cart
        )
        has_digital_items = any(
            item.get("product_type") in {"photo", "video"}
            for item in cart
        )

        accepts_terms = request.data.get("accepts_terms") is True
        accepts_privacy = request.data.get("accepts_privacy") is True
        accepts_personal_use = request.data.get("accepts_personal_use") is True
        if settings.CHECKOUT_REQUIRE_TERMS_ACCEPTANCE:
            acceptance_errors = {}
            if not accepts_terms:
                acceptance_errors["accepts_terms"] = "Accept the Terms & Conditions before payment."
            if not accepts_privacy:
                acceptance_errors["accepts_privacy"] = "Confirm the privacy acknowledgement before payment."
            if has_digital_items and not accepts_personal_use:
                acceptance_errors["accepts_personal_use"] = "Accept the personal-use licence terms before payment."
            if acceptance_errors:
                return Response(
                    {"code": "CHECKOUT_ACCEPTANCE_REQUIRED", **acceptance_errors},
                    status=status.HTTP_400_BAD_REQUEST,
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
            pricing = _resolve_cart_pricing(cart)
            total = pricing["cart_total"]
            physical_line_items = pricing["physical_line_items"]
            eligible_physical_subtotal = pricing["eligible_physical_subtotal"]
            pricing_snapshot = pricing["pricing_snapshot"]
        except DRFValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
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
        
        try:
            shipping_quote = calculate_physical_shipping_quote(
                line_items=physical_line_items,
                shipping_country=shipping_country,
                shipping_method=shipping_method,
            )
        except ShippingConfigurationError as exc:
            logger.warning(
                "CreatePaymentIntentView blocked checkout because shipping configuration was missing "
                "(country=%s, method=%s, cart_items=%s)",
                shipping_country,
                shipping_method,
                len(physical_line_items),
            )
            return Response(
                {
                    "code": "SHIPPING_UNAVAILABLE",
                    "error": str(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        shipping_cost = shipping_quote.delivery_cost
        customer_email = _extract_checkout_customer_email(
            request=request,
            shipping_details=shipping_details,
        )
        if not customer_email:
            return Response(
                {
                    "code": (
                        "ACCOUNT_EMAIL_REQUIRED"
                        if request.user.is_authenticated
                        else "EMAIL_REQUIRED"
                    ),
                    "error": (
                        "Add a valid email address to your account before checkout."
                        if request.user.is_authenticated
                        else "Add a valid email address before checkout."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_discount_code = request.data.get("discount_code")
        discount_result = evaluate_discount(
            code=requested_discount_code,
            customer_email=customer_email,
            eligible_physical_subtotal=eligible_physical_subtotal,
        )
        if normalize_discount_code(requested_discount_code) and not discount_result.valid:
            return Response(
                {
                    "code": "DISCOUNT_INVALID",
                    "error": discount_result.message or "Invalid discount code.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        grand_total = total + shipping_cost - discount_result.amount
        amount_in_cents = int(
            (grand_total * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

        profile = None
        if request.user.is_authenticated:
            profile = UserProfile.objects.filter(user=request.user).first()
        now = timezone.now()
        fingerprint_payload = {
            "cart": cart,
            "shipping_details": shipping_details if has_physical_items else {},
            "shipping_method": shipping_method if has_physical_items else "budget",
            "customer_email": customer_email,
            "user_profile_id": profile.id if profile else None,
            "save_info": bool(request.data.get("save_info", False)),
            "shipping_cost": str(shipping_cost),
            "discount_code": discount_result.code,
            "discount_amount": str(discount_result.amount),
            "pricing_snapshot": pricing_snapshot,
            "expected_amount_cents": amount_in_cents,
            "accepts_terms": accepts_terms,
            "accepts_privacy": accepts_privacy,
            "accepts_personal_use": accepts_personal_use if has_digital_items else False,
        }
        request_fingerprint = build_request_fingerprint(fingerprint_payload)

        attempt_defaults = {
            "user_profile": profile,
            "request_fingerprint": request_fingerprint,
            "cart_snapshot": cart,
            "pricing_snapshot": pricing_snapshot,
            "shipping_details_snapshot": shipping_details if has_physical_items else {},
            "shipping_method": shipping_method if has_physical_items else "budget",
            "customer_email": customer_email,
            "save_info": bool(request.data.get("save_info", False)),
            "shipping_cost": shipping_cost,
            "free_shipping_applied": shipping_quote.free_shipping_applied,
            "discount_code": discount_result.code,
            "discount_amount": discount_result.amount,
            "discount_percent": discount_result.percent,
            "discount_label": discount_result.label,
            "expected_amount_cents": amount_in_cents,
            "currency": "eur",
            "terms_accepted_at": now if accepts_terms else None,
            "terms_version": settings.CHECKOUT_TERMS_VERSION if accepts_terms else "",
            "privacy_accepted_at": now if accepts_privacy else None,
            "privacy_version": settings.CHECKOUT_PRIVACY_VERSION if accepts_privacy else "",
            "personal_use_accepted_at": now if has_digital_items and accepts_personal_use else None,
            "personal_terms_version": get_personal_terms_version() if has_digital_items and accepts_personal_use else "",
        }

        try:
            attempt, created = CheckoutAttempt.objects.get_or_create(
                checkout_key=checkout_key,
                defaults=attempt_defaults,
            )
            if not created and attempt.request_fingerprint != request_fingerprint:
                return Response(
                    {
                        "code": "CHECKOUT_STATE_CHANGED",
                        "error": "Checkout details changed. Prepare payment again.",
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            if attempt.payment_intent_id:
                intent = stripe.PaymentIntent.retrieve(attempt.payment_intent_id)
                return self._response_for_attempt(attempt, intent)

            metadata = {
                "checkout_attempt_id": str(attempt.id),
                "checkout_flow": "store_checkout_v2",
            }

            intent = stripe.PaymentIntent.create(
                amount=amount_in_cents,
                currency="eur",
                payment_method_types=_configured_stripe_payment_method_types(),
                metadata=metadata,
                receipt_email=customer_email,
                idempotency_key=f"checkout-{checkout_key}",
            )

            intent_id = self._intent_value(intent, "id")
            if isinstance(intent_id, str) and intent_id:
                try:
                    CheckoutAttempt.objects.filter(
                        pk=attempt.pk,
                        payment_intent_id__isnull=True,
                    ).update(payment_intent_id=intent_id)
                except IntegrityError:
                    logger.exception(
                        "PaymentIntent was already attached to another checkout attempt. intent_id=%s",
                        intent_id,
                    )
                    raise
                attempt.payment_intent_id = intent_id

            return self._response_for_attempt(attempt, intent)
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


class DiscountValidationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = "discount_validation"

    def post(self, request, *args, **kwargs):
        cart = request.data.get("cart")
        if not isinstance(cart, list) or not cart:
            return Response(
                {"code": "INVALID_CART_PAYLOAD", "error": "Cart is empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_discount_code = request.data.get("discount_code")
        if not normalize_discount_code(requested_discount_code):
            return Response(
                {"code": "DISCOUNT_REQUIRED", "error": "Discount code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = _extract_checkout_customer_email(
            request=request,
            shipping_details={"email": request.data.get("email")},
        )
        if not email:
            return Response(
                {
                    "code": "EMAIL_REQUIRED",
                    "error": "Email address is required to validate this welcome code.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pricing = _resolve_cart_pricing(cart)
        except DRFValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        except (KeyError, TypeError, ValueError, ObjectDoesNotExist):
            return Response(
                {
                    "code": "INVALID_CART_PAYLOAD",
                    "error": "Invalid cart data provided.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        discount_result = evaluate_discount(
            code=requested_discount_code,
            customer_email=email,
            eligible_physical_subtotal=pricing["eligible_physical_subtotal"],
        )
        if not discount_result.valid:
            return Response(
                {
                    "code": "DISCOUNT_INVALID",
                    "error": discount_result.message or "Invalid discount code.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "code": discount_result.code,
                "discountAmount": float(discount_result.amount),
                "discountPercent": float(discount_result.percent),
                "discountLabel": discount_result.label,
                "eligibleSubtotal": float(pricing["eligible_physical_subtotal"]),
            },
            status=status.HTTP_200_OK,
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

    def _order_has_physical_items(self, order):
        return order.items.filter(content_type__model='productvariant').exists()

    def _extract_payment_link_id(self, session):
        return session.get('payment_link') or session.get('metadata', {}).get('payment_link_id')

    def _build_license_download_url(self, request, token_obj):
        path = reverse('license-asset-download', args=[str(token_obj.token)])
        base_url = getattr(settings, "LICENCE_DOWNLOAD_BASE_URL", None)
        if base_url:
            return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        return request.build_absolute_uri(path)

    def _build_personal_download_url(self, request, token_obj):
        path = reverse('personal-asset-download', args=[str(token_obj.token)])
        base_url = getattr(settings, "PERSONAL_DOWNLOAD_BASE_URL", None)
        if base_url:
            return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        return request.build_absolute_uri(path)

    def _build_profile_url(self):
        frontend_url = getattr(settings, "FRONTEND_URL", None)
        if frontend_url:
            return urljoin(str(frontend_url).rstrip("/") + "/", "profile")
        logger.warning("FRONTEND_URL is not configured; omitting profile link from confirmation email")
        return None

    def _mark_confirmation_email_sent(self, order):
        order.confirmation_email_status = 'SENT'
        order.confirmation_email_sent_at = timezone.now()
        order.confirmation_email_failed_at = None
        order.confirmation_email_error = ""
        order.save(
            update_fields=[
                'confirmation_email_status',
                'confirmation_email_sent_at',
                'confirmation_email_failed_at',
                'confirmation_email_error',
            ]
        )

    def _mark_confirmation_email_failed(self, order, exc):
        order.confirmation_email_status = 'FAILED'
        order.confirmation_email_failed_at = timezone.now()
        order.confirmation_email_error = f"{exc.__class__.__name__}: {exc}"
        order.save(
            update_fields=[
                'confirmation_email_status',
                'confirmation_email_failed_at',
                'confirmation_email_error',
            ]
        )

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
        asset_file_name = get_asset_file_name(asset)
        if not asset_file_name:
            raise RuntimeError(f"No deliverable asset file is attached to asset {asset}")
        asset_file = open_asset_file(asset, "rb")
        if not asset_file:
            raise RuntimeError(f"Deliverable asset file is unavailable for asset {asset}")
        try:
            asset_file.close()
        except Exception:
            pass

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

    def _handle_realestate_deposit_payment(self, session):
        metadata = session.get('metadata') or {}
        if metadata.get('purpose') != 'realestate_deposit':
            return False

        payment_status = session.get('payment_status')
        if payment_status != 'paid':
            logger.warning(
                "Real estate deposit checkout session not paid yet (status=%s). Skipping. session_id=%s",
                payment_status,
                session.get('id') or "unknown",
            )
            return True

        checkout_session_id = str(session.get('id') or "").strip()
        enquiry_id = str(metadata.get('realestate_enquiry_id') or "").strip()
        if not enquiry_id:
            raise RuntimeError(
                "Paid real estate deposit checkout session is missing "
                "realestate_enquiry_id metadata."
            )

        enquiry = (
            RealEstateEnquiry.objects
            .select_for_update()
            .filter(pk=enquiry_id)
            .first()
        )
        if enquiry is None:
            raise RuntimeError(
                "Paid real estate deposit checkout session referenced "
                f"unknown enquiry {enquiry_id}."
            )

        stored_session_id = str(enquiry.stripe_deposit_session_id or "").strip()
        if stored_session_id and checkout_session_id != stored_session_id:
            raise RuntimeError(
                "Paid real estate deposit checkout session did not match "
                "the stored enquiry session."
            )

        expected_amount_cents = int(
            (
                calculate_realestate_deposit_amounts(enquiry)["deposit_amount"]
                * Decimal("100")
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        amount_total = int(session.get('amount_total') or 0)
        currency = str(session.get('currency') or "").lower()
        if amount_total != expected_amount_cents or currency != "eur":
            raise RuntimeError(
                "Paid real estate deposit amount did not match the "
                "expected booking deposit."
            )

        if enquiry.deposit_paid:
            logger.info(
                "Real estate deposit already marked paid; skipping. enquiry_id=%s",
                enquiry.pk,
            )
            return True

        paid_at = timezone.now()
        update_fields = ["deposit_paid", "deposit_paid_at", "updated_at"]
        enquiry.deposit_paid = True
        enquiry.deposit_paid_at = paid_at

        if checkout_session_id and not enquiry.stripe_deposit_session_id:
            enquiry.stripe_deposit_session_id = checkout_session_id
            update_fields.append("stripe_deposit_session_id")

        if enquiry.status not in {
            RealEstateEnquiry.Status.BOOKED,
            RealEstateEnquiry.Status.COMPLETED,
            RealEstateEnquiry.Status.CLOSED,
            RealEstateEnquiry.Status.SPAM,
        }:
            enquiry.status = RealEstateEnquiry.Status.BOOKED
            update_fields.append("status")

        enquiry.save(update_fields=update_fields)
        logger.info(
            "Real estate deposit marked paid. enquiry_id=%s session_id=%s",
            enquiry.pk,
            checkout_session_id or "unknown",
        )
        return True

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
        retryable_processing_error = False
        attempt_id = None

        try:
            if event_type == 'payment_intent.succeeded':
                payment_intent = event['data']['object']

                metadata = payment_intent.get('metadata', {})
                payment_intent_id = str(payment_intent.get('id') or '')
                attempt = None
                attempt_id = metadata.get("checkout_attempt_id")
                if attempt_id:
                    try:
                        attempt = (
                            CheckoutAttempt.objects
                            .select_related("user_profile__user")
                            .get(pk=int(attempt_id))
                        )
                    except (TypeError, ValueError, CheckoutAttempt.DoesNotExist):
                        raise DRFValidationError(
                            {"payment": "The checkout attempt could not be verified."}
                        )
                    if attempt.payment_intent_id != payment_intent_id:
                        raise DRFValidationError(
                            {"payment": "The PaymentIntent does not match its checkout attempt."}
                        )

                    amount_received = int(payment_intent.get("amount_received") or 0)
                    currency = str(payment_intent.get("currency") or "").lower()
                    if (
                        amount_received != attempt.expected_amount_cents
                        or currency != attempt.currency
                    ):
                        logger.error(
                            "Paid amount did not match checkout attempt; blocking order creation. "
                            "event_id=%s intent_id=%s expected=%s received=%s currency=%s",
                            event_id,
                            payment_intent_id,
                            attempt.expected_amount_cents,
                            amount_received,
                            currency,
                        )
                        raise DRFValidationError(
                            {"payment": "Paid amount did not match the validated checkout total."}
                        )

                    _validate_checkout_attempt_pricing(attempt)

                    cart_items = attempt.cart_snapshot
                    shipping_details = attempt.shipping_details_snapshot
                    address_details = shipping_details.get('address') or {}
                    shipping_cost = attempt.shipping_cost
                    shipping_method = attempt.shipping_method
                    discount_code = attempt.discount_code
                    discount_amount = attempt.discount_amount
                    discount_percent = attempt.discount_percent
                    discount_label = attempt.discount_label
                    order_email = attempt.customer_email
                    profile = attempt.user_profile
                    save_info = attempt.save_info
                else:
                    # Legacy fallback for PaymentIntents created before checkout snapshots.
                    cart_items_str = metadata.get('cart', '[]')
                    shipping_details = payment_intent.get('shipping') or {}
                    address_details = shipping_details.get('address') or {}
                    shipping_cost = float(metadata.get('shipping_cost', 0.00))
                    shipping_method = metadata.get('shipping_method', 'budget')
                    discount_code = normalize_discount_code(metadata.get('discount_code'))
                    discount_amount = _safe_decimal_from_metadata(
                        metadata.get('discount_amount', '0'),
                        field_name="discount_amount",
                        event_id=event_id,
                    )
                    discount_percent = _safe_decimal_from_metadata(
                        metadata.get('discount_percent', '0'),
                        field_name="discount_percent",
                        event_id=event_id,
                    )
                    discount_label = str(metadata.get('discount_label', '') or '').strip()
                    try:
                        cart_items = json.loads(cart_items_str)
                    except (TypeError, ValueError):
                        cart_items = []
                    order_email = payment_intent.get('receipt_email')
                    if not order_email or '@' not in order_email:
                        order_email = metadata.get('username')
                        if not order_email or '@' not in order_email:
                            order_email = "guest@example.com"
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

                if not cart_items:
                    logger.info("No cart items found for payment_intent; skipping order creation.")
                    return Response(status=status.HTTP_200_OK)

                order_data = {
                    'stripe_pid': payment_intent_id,
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
                    'shipping_method': shipping_method,
                    'discount_code': discount_code,
                    'discount_amount': discount_amount,
                    'discount_percent': discount_percent,
                    'discount_label': discount_label,
                }
                if attempt is not None:
                    order_data['checkout_attempt'] = attempt.id

                if profile is not None:
                    order_data['user_profile'] = profile.id
                    account_email = _validated_email_or_none(profile.user.email)
                    if account_email:
                        order_data['email'] = account_email
                    else:
                        logger.warning(
                            "Authenticated webhook order kept Stripe email because account email was blank or invalid. "
                            "user_id=%s event_id=%s",
                            profile.user_id,
                            event_id,
                        )

                    if save_info:
                        profile.default_phone_number = shipping_details.get('phone', profile.default_phone_number)
                        profile.default_street_address1 = address_details.get('line1', profile.default_street_address1)
                        profile.default_street_address2 = address_details.get('line2', profile.default_street_address2)
                        profile.default_town = address_details.get('city', profile.default_town)
                        profile.default_postcode = address_details.get('postal_code', profile.default_postcode)
                        profile.default_county = address_details.get('state', profile.default_county)
                        profile.default_country = address_details.get('country', profile.default_country)
                        profile.save()

                existing_order = None
                if attempt is not None:
                    existing_order = Order.objects.filter(checkout_attempt=attempt).first()
                if existing_order is None:
                    existing_order = Order.objects.filter(
                        stripe_pid=payment_intent_id
                    ).first()
                order = existing_order

                if order is None:
                    serializer_context = {}
                    if attempt is not None:
                        serializer_context = {
                            "pricing_snapshot": attempt.pricing_snapshot,
                            "shipping_cost_snapshot": attempt.shipping_cost,
                        }
                    serializer = OrderSerializer(
                        data=order_data,
                        context=serializer_context,
                    )
                    if serializer.is_valid():
                        try:
                            order = serializer.save()
                        except IntegrityError:
                            order = Order.objects.filter(
                                stripe_pid=payment_intent_id
                            ).first()
                            if order is None:
                                raise
                        logger.info("Order created successfully. order_number=%s", order.order_number)
                    else:
                        error_fields = self._summarize_validation_errors(serializer.errors)
                        logger.error("Error creating order. Validation fields: %s", error_fields)
                        processing_error = f"Order validation failed. Fields: {error_fields}"
                else:
                    logger.info(
                        "Reusing existing order for Stripe retry. order_number=%s stripe_pid=%s",
                        order.order_number,
                        order.stripe_pid,
                    )

                if order and not processing_error and discount_code:
                    try:
                        record_discount_redemption(order, reject_conflict=True)
                    except DiscountRedemptionConflict:
                        order.fulfilment_hold_reason = "DISCOUNT_ALREADY_REDEEMED"
                        order.save(update_fields=["fulfilment_hold_reason"])
                        processing_error = (
                            f"Order {order.order_number} requires review because the "
                            "discount was already redeemed."
                        )
                        logger.error(
                            "Holding paid order because its one-time discount was already redeemed. "
                            "order_number=%s discount_code=%s",
                            order.order_number,
                            order.discount_code,
                        )

                if order and not processing_error:
                    if discount_code and order.discount_code != discount_code:
                        order.discount_code = discount_code
                        order.discount_amount = discount_amount
                        order.discount_percent = discount_percent
                        order.discount_label = discount_label
                        order.total_price = order.order_total + order.delivery_cost - order.discount_amount
                        order.save(
                            update_fields=[
                                "discount_code",
                                "discount_amount",
                                "discount_percent",
                                "discount_label",
                                "total_price",
                            ]
                        )

                    email_claimed = Order.objects.filter(pk=order.pk).exclude(
                        confirmation_email_status__in=['SENDING', 'SENT']
                    ).update(confirmation_email_status='SENDING')
                    if email_claimed:
                        order.confirmation_email_status = 'SENDING'
                        try:
                            send_order_confirmation_email(order, request=request)
                            self._mark_confirmation_email_sent(order)
                            logger.info("Confirmation email sent. order_number=%s", order.order_number)
                        except Exception as exc:
                            self._mark_confirmation_email_failed(order, exc)
                            logger.exception(
                                "Could not send confirmation email. order_number=%s",
                                order.order_number,
                            )
                    else:
                        logger.info(
                            "Skipping confirmation email because it is already sending or sent. "
                            "order_number=%s",
                            order.order_number,
                        )
                    try:
                        if self._order_has_physical_items(order):
                            order.refresh_from_db(
                                fields=[
                                    'prodigi_order_id',
                                    'prodigi_status',
                                    'prodigi_submission_started_at',
                                ]
                            )
                            if order.prodigi_order_id:
                                if order.prodigi_submission_started_at:
                                    order.prodigi_submission_started_at = None
                                    order.save(
                                        update_fields=['prodigi_submission_started_at']
                                    )
                                logger.info(
                                    "Skipping Prodigi fulfillment because the order already has a Prodigi id. "
                                    "order_number=%s prodigi_order_id=%s",
                                    order.order_number,
                                    order.prodigi_order_id,
                                )
                            else:
                                lease_seconds = max(
                                    int(
                                        getattr(
                                            settings,
                                            'PRODIGI_SUBMISSION_LEASE_SECONDS',
                                            300,
                                        )
                                    ),
                                    30,
                                )
                                claim_time = timezone.now()
                                stale_before = claim_time - timedelta(
                                    seconds=lease_seconds
                                )
                                claimable_submission = (
                                    Q(prodigi_status__isnull=True)
                                    | Q(prodigi_status='')
                                    | ~Q(
                                        prodigi_status__in=[
                                            'SUBMITTING',
                                            'SUBMITTED',
                                        ]
                                    )
                                    | Q(
                                        prodigi_status='SUBMITTING',
                                        prodigi_submission_started_at__isnull=True,
                                    )
                                    | Q(
                                        prodigi_status='SUBMITTING',
                                        prodigi_submission_started_at__lte=stale_before,
                                    )
                                )
                                fulfilment_claimed = Order.objects.filter(
                                    claimable_submission,
                                    Q(prodigi_order_id__isnull=True)
                                    | Q(prodigi_order_id=''),
                                    pk=order.pk,
                                ).update(
                                    prodigi_status='SUBMITTING',
                                    prodigi_submission_started_at=claim_time,
                                )
                                if not fulfilment_claimed:
                                    logger.info(
                                        "Skipping Prodigi fulfillment because submission is already in progress. "
                                        "order_number=%s",
                                        order.order_number,
                                    )
                                else:
                                    order.prodigi_status = 'SUBMITTING'
                                    order.prodigi_submission_started_at = claim_time
                                    logger.info("Sending order to Prodigi. order_number=%s", order.order_number)
                                    prodigi_response = create_prodigi_order(order)
                                    if isinstance(prodigi_response, dict):
                                        prodigi_order = prodigi_response.get("order")
                                        if isinstance(prodigi_order, dict):
                                            accepted_order_id = str(
                                                prodigi_order.get("id") or ""
                                            ).strip()
                                            if accepted_order_id:
                                                order.prodigi_order_id = accepted_order_id
                                                order.save(
                                                    update_fields=["prodigi_order_id"]
                                                )
                                            sync_order_shipping_from_prodigi(order, prodigi_order)
                                    order.refresh_from_db(
                                        fields=[
                                            'prodigi_order_id',
                                            'prodigi_status',
                                            'prodigi_submission_started_at',
                                        ]
                                    )
                                    if not order.prodigi_order_id and order.prodigi_status == 'SUBMITTING':
                                        order.prodigi_status = 'SUBMITTED'
                                    order.prodigi_submission_started_at = None
                                    order.save(
                                        update_fields=[
                                            'prodigi_status',
                                            'prodigi_submission_started_at',
                                        ]
                                    )
                                    logger.info("Order sent to Prodigi successfully. order_number=%s", order.order_number)
                        else:
                            logger.info(
                                "Digital-only order detected; skipping Prodigi fulfillment. order_number=%s",
                                order.order_number,
                            )
                    except Exception as exc:
                        prodigi_order_id = str(order.prodigi_order_id or "").strip()
                        if prodigi_order_id:
                            if order.prodigi_status in (None, "", "SUBMITTING", "FULFILMENT_FAILED"):
                                order.prodigi_status = "SUBMITTED"
                        elif order.prodigi_status != "FULFILMENT_FAILED":
                            order.prodigi_status = "FULFILMENT_FAILED"
                        order.prodigi_submission_started_at = None
                        update_fields = [
                            "prodigi_status",
                            "prodigi_submission_started_at",
                        ]
                        if prodigi_order_id:
                            update_fields.append("prodigi_order_id")
                        try:
                            order.save(update_fields=update_fields)
                        except Exception:
                            logger.exception(
                                "Failed to persist paid-order fulfilment failure state. "
                                "order_number=%s",
                                order.order_number,
                            )
                        try:
                            send_fulfilment_failure_alert(order, exc)
                        except Exception:
                            logger.exception(
                                "Failed to send paid-order fulfilment alert. order_number=%s",
                                order.order_number,
                            )
                        if prodigi_order_id:
                            processing_error = (
                                "Prodigi accepted physical order "
                                f"{order.order_number}, but local post-submission processing failed: {exc}"
                            )
                        else:
                            processing_error = (
                                f"Physical fulfilment failed for order {order.order_number}: {exc}"
                            )
                        retryable_processing_error = True
                        logger.exception(
                            "Failed to fulfill order after webhook processing. order_number=%s",
                            order.order_number,
                        )
                    if not discount_code:
                        try:
                            record_discount_redemption(order)
                        except Exception:
                            logger.exception(
                                "Failed to record discount redemption. order_number=%s discount_code=%s",
                                order.order_number,
                                order.discount_code,
                            )

            elif event_type == 'checkout.session.completed':
                session = event['data']['object']
                with transaction.atomic():
                    handled_realestate_deposit = self._handle_realestate_deposit_payment(session)
                if not handled_realestate_deposit:
                    self._handle_license_payment(request, session)
        except DRFValidationError as e:
            processing_error = str(e.detail if hasattr(e, "detail") else e)
            # A signed, successful PaymentIntent backed by a checkout snapshot
            # represents captured customer funds. Ask Stripe to retry rather
            # than acknowledging a failure that left no fulfilment record.
            if event_type == 'payment_intent.succeeded' and attempt_id:
                retryable_processing_error = True
            logger.exception("Validation error processing Stripe event. event_id=%s", event_id)
        except Exception as e:
            processing_error = str(e)
            retryable_processing_error = True
            logger.exception("Error processing Stripe event. event_id=%s", event_id)
        finally:
            if event_record and should_process_event:
                StripeWebhookEvent.objects.filter(pk=event_record.pk).update(
                    status='FAILED' if processing_error else 'SUCCESS',
                    processed_at=timezone.now(),
                    error_message=processing_error,
                    event_type=event_type or 'unknown',
                )
        
        if processing_error and retryable_processing_error:
            return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
        try:
            claim_guest_orders_for_user(self.request.user)
        except Exception:
            logger.exception(
                "Guest order claiming failed during order history fetch. user_id=%s",
                self.request.user.id,
            )
        return Order.objects.filter(user_profile=self.request.user.userprofile).order_by('-date')


class CloudEventJSONParser(JSONParser):
    media_type = "application/cloudevents+json"


class ProdigiCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = "prodigi_callback"
    parser_classes = [JSONParser, CloudEventJSONParser]

    def _get_expected_callback_token(self):
        return str(getattr(settings, "PRODIGI_CALLBACK_TOKEN", "") or "").strip()

    def _get_provided_callback_token(self, request):
        query_token = str(request.query_params.get("token") or "").strip()
        if query_token:
            return query_token
        return str(request.META.get("HTTP_X_PRODIGI_CALLBACK_TOKEN") or "").strip()

    def _reject_invalid_callback_token(self, request, *, prodigi_order_id, event_type):
        expected_token = self._get_expected_callback_token()
        if not expected_token:
            return None

        provided_token = self._get_provided_callback_token(request)
        if secrets.compare_digest(provided_token, expected_token):
            return None

        logger.warning(
            "Prodigi callback rejected due to invalid callback token (event_type=%s prodigi_order_id=%s)",
            event_type or "unknown",
            prodigi_order_id or "n/a",
        )
        return Response(status=status.HTTP_403_FORBIDDEN)

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        event_type = str(payload.get("type") or "").strip()
        prodigi_order_hint = None
        data_payload = payload.get("data")
        if isinstance(data_payload, dict):
            nested_order = data_payload.get("order")
            if isinstance(nested_order, dict):
                prodigi_order_hint = nested_order
            else:
                prodigi_order_hint = data_payload
        elif isinstance(payload.get("order"), dict):
            prodigi_order_hint = payload.get("order")
        if not isinstance(prodigi_order_hint, dict):
            logger.warning("Prodigi callback received invalid payload shape.")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        prodigi_order_id = str(
            prodigi_order_hint.get("id")
            or payload.get("subject")
            or ""
        ).strip()
        if not prodigi_order_id:
            logger.warning("Prodigi callback received payload without order id.")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        invalid_token_response = self._reject_invalid_callback_token(
            request,
            prodigi_order_id=prodigi_order_id,
            event_type=event_type,
        )
        if invalid_token_response is not None:
            return invalid_token_response

        payload_stage = ""
        if isinstance(prodigi_order_hint.get("status"), dict):
            payload_stage = str(prodigi_order_hint["status"].get("stage") or "").strip()
        logger.info(
            "Prodigi callback received (event_type=%s payload_stage=%s prodigi_order_id=%s)",
            event_type or "unknown",
            payload_stage or "n/a",
            prodigi_order_id,
        )

        try:
            prodigi_order = fetch_prodigi_order(prodigi_order_id)
        except RuntimeError:
            logger.exception(
                "Prodigi callback could not verify order against Prodigi API (prodigi_order_id=%s)",
                prodigi_order_id,
            )
            return Response(status=status.HTTP_502_BAD_GATEWAY)

        merchant_reference = str(prodigi_order.get("merchantReference") or "").strip()
        with transaction.atomic():
            order = None
            if prodigi_order_id:
                order = Order.objects.select_for_update().filter(prodigi_order_id=prodigi_order_id).first()
            if order is None and merchant_reference:
                order = Order.objects.select_for_update().filter(order_number=merchant_reference).first()
            if order is None:
                logger.warning(
                    "Prodigi callback ignored because no local order matched (prodigi_order_id=%s merchant_reference=%s)",
                    prodigi_order_id or "n/a",
                    merchant_reference or "n/a",
                )
                return Response(status=status.HTTP_200_OK)
            initial_order_stage = "n/a"
            if isinstance(prodigi_order.get("status"), dict):
                initial_order_stage = (
                    str(prodigi_order["status"].get("stage") or "").strip() or "n/a"
                )
            logger.info(
                "Prodigi callback matched local order (event_type=%s prodigi_order_id=%s merchant_reference=%s local_order_id=%s order_number=%s order_stage=%s)",
                event_type or "unknown",
                prodigi_order_id,
                merchant_reference or "n/a",
                order.id,
                order.order_number,
                initial_order_stage,
            )

            try:
                sync_result = sync_order_shipping_from_prodigi(
                    order,
                    prodigi_order,
                    mark_callback_received=True,
                )
            except Exception:
                logger.exception(
                    "Failed to sync shipping data after Prodigi callback (event_type=%s prodigi_order_id=%s order=%s)",
                    event_type or "unknown",
                    prodigi_order_id,
                    order.order_number,
                )
                return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            if not sync_result["tracking_signature"]:
                logger.info(
                    "Prodigi callback updated order but skipped shipping email because order is not yet shipped/dispatched (event_type=%s prodigi_order_id=%s order=%s order_stage=%s tracked_shipments=%s)",
                    event_type or "unknown",
                    prodigi_order_id,
                    order.order_number,
                    sync_result["order_stage"] or "n/a",
                    len(sync_result["tracked_shipments"]),
                )
                return Response(status=status.HTTP_200_OK)

            if sync_result["email_skipped_reason"] == "duplicate_signature":
                logger.info(
                    "Prodigi shipping email already sent for current shipment state (event_type=%s prodigi_order_id=%s order=%s)",
                    event_type or "unknown",
                    prodigi_order_id,
                    order.order_number,
                )
                return Response(status=status.HTTP_200_OK)

            if sync_result["email_sent"]:
                logger.info(
                    "Prodigi shipping email sent (event_type=%s prodigi_order_id=%s order=%s order_stage=%s tracked_shipments=%s)",
                    event_type or "unknown",
                    prodigi_order_id,
                    order.order_number,
                    sync_result["order_stage"] or "n/a",
                    len(sync_result["tracked_shipments"]),
                )
            else:
                logger.info(
                    "Prodigi shipping email skipped after evaluation (event_type=%s prodigi_order_id=%s order=%s order_stage=%s tracked_shipments=%s reason=%s)",
                    event_type or "unknown",
                    prodigi_order_id,
                    order.order_number,
                    sync_result["order_stage"] or "n/a",
                    len(sync_result["tracked_shipments"]),
                    sync_result["email_skipped_reason"] or "unknown",
                )

        return Response(status=status.HTTP_200_OK)
