from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import PersonalDownloadToken


DEFAULT_TOKEN_DAYS = int(getattr(settings, "PERSONAL_DOWNLOAD_TOKEN_DAYS", 7))


def ensure_personal_download_token(order_item, days=None):
    days = days or DEFAULT_TOKEN_DAYS
    now = timezone.now()
    existing = (
        PersonalDownloadToken.objects.filter(
            order_item=order_item,
            used_at__isnull=True,
            expires_at__gt=now,
        )
        .order_by("-expires_at")
        .first()
    )
    if existing:
        return existing

    expires_at = now + timedelta(days=days)
    return PersonalDownloadToken.objects.create(
        order_item=order_item,
        expires_at=expires_at,
    )
