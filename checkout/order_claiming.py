from django.db.models.functions import Lower, Trim

from .models import Order

UNCLAIMABLE_ORDER_EMAILS = {"guest@example.com"}


def _normalized_email(value):
    return str(value or "").strip().lower()


def claim_guest_orders_for_user(user):
    """
    Attach previously guest-owned orders to the authenticated user's profile
    by matching the normalized order email to the user's normalized email.
    Safe to call repeatedly.
    """
    normalized_email = _normalized_email(getattr(user, "email", ""))
    if not normalized_email or normalized_email in UNCLAIMABLE_ORDER_EMAILS:
        return 0

    user_profile = getattr(user, "userprofile", None)
    if user_profile is None:
        return 0

    return (
        Order.objects.filter(user_profile__isnull=True)
        .annotate(normalized_order_email=Lower(Trim("email")))
        .exclude(normalized_order_email__in=UNCLAIMABLE_ORDER_EMAILS)
        .filter(normalized_order_email=normalized_email)
        .update(user_profile=user_profile)
    )
