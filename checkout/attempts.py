import hashlib
import json
import uuid

from rest_framework.exceptions import ValidationError


MAX_CHECKOUT_ITEMS = 50
MAX_ITEM_QUANTITY = 100
SUPPORTED_PRODUCT_TYPES = {"physical", "photo", "video"}


def normalize_checkout_key(value):
    if value in (None, ""):
        return uuid.uuid4()
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError(
            {
                "code": "INVALID_CHECKOUT_ID",
                "error": "Invalid checkout request identifier.",
            }
        )


def canonicalize_cart(cart):
    if not isinstance(cart, list) or not cart:
        raise ValidationError(
            {"code": "INVALID_CART_PAYLOAD", "error": "Cart is empty."}
        )
    if len(cart) > MAX_CHECKOUT_ITEMS:
        raise ValidationError(
            {
                "code": "CART_TOO_LARGE",
                "error": f"A maximum of {MAX_CHECKOUT_ITEMS} distinct items is supported.",
            }
        )

    canonical = []
    for raw_item in cart:
        if not isinstance(raw_item, dict):
            raise ValidationError(
                {
                    "code": "INVALID_CART_PAYLOAD",
                    "error": "Invalid cart item payload. Expected an object.",
                }
            )

        product_type = str(raw_item.get("product_type") or "").strip().lower()
        if product_type not in SUPPORTED_PRODUCT_TYPES:
            raise ValidationError(
                {
                    "code": "INVALID_CART_PAYLOAD",
                    "error": "One or more cart items have an unsupported product type.",
                }
            )
        try:
            product_id = int(raw_item["product_id"])
            quantity = int(raw_item.get("quantity", 1))
        except (KeyError, TypeError, ValueError):
            raise ValidationError(
                {
                    "code": "INVALID_CART_PAYLOAD",
                    "error": "Invalid cart data provided.",
                }
            )
        if product_id < 1 or quantity < 1 or quantity > MAX_ITEM_QUANTITY:
            raise ValidationError(
                {
                    "code": "INVALID_CART_PAYLOAD",
                    "error": "Cart quantity must be a whole number of at least 1.",
                }
            )
        if product_type in {"photo", "video"}:
            raw_options = raw_item.get("options") or {}
            if not isinstance(raw_options, dict):
                raise ValidationError(
                    {
                        "code": "INVALID_CART_PAYLOAD",
                        "error": f"Invalid options payload for digital item {product_id}.",
                    }
                )
            quantity = 1

        item = {
            "product_id": product_id,
            "product_type": product_type,
            "quantity": quantity,
        }
        if product_type == "physical":
            raw_options = raw_item.get("options") or {}
            if not isinstance(raw_options, dict):
                raise ValidationError(
                    {
                        "code": "INVALID_CART_PAYLOAD",
                        "error": "Invalid print options payload.",
                    }
                )
            item["options"] = {
                key: str(raw_options[key]).strip()
                for key in ("material", "size")
                if raw_options.get(key) not in (None, "")
            }
        canonical.append(item)
    return canonical


def canonicalize_shipping_details(value):
    if not isinstance(value, dict):
        return {}
    raw_address = value.get("address")
    address = raw_address if isinstance(raw_address, dict) else {}
    return {
        "name": str(value.get("name") or "").strip()[:150],
        "email": str(value.get("email") or "").strip().lower()[:254],
        "phone": str(value.get("phone") or "").strip()[:20],
        "address": {
            "line1": str(address.get("line1") or "").strip()[:255],
            "line2": str(address.get("line2") or "").strip()[:255],
            "city": str(address.get("city") or "").strip()[:100],
            "state": str(address.get("state") or "").strip()[:100],
            "country": str(address.get("country") or "").strip().upper()[:2],
            "postal_code": str(address.get("postal_code") or "").strip()[:20],
        },
    }


def build_request_fingerprint(payload):
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
