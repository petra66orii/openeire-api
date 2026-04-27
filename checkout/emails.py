from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse

from products.personal_downloads import ensure_personal_download_token
from products.personal_licence import (
    get_personal_licence_summary,
    get_personal_licence_url,
)


def _build_personal_download_url(request, token_obj):
    path = reverse('personal-asset-download', args=[str(token_obj.token)])
    base_url = getattr(settings, "PERSONAL_DOWNLOAD_BASE_URL", None)
    if base_url:
        return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if request is None:
        raise RuntimeError(
            "PERSONAL_DOWNLOAD_BASE_URL must be configured when resending confirmation emails without a request context."
        )
    return request.build_absolute_uri(path)


def _build_profile_url():
    frontend_url = getattr(settings, "FRONTEND_URL", None)
    if frontend_url:
        return urljoin(str(frontend_url).rstrip("/") + "/", "profile")
    return None


def send_order_confirmation_email(order, request=None):
    cust_email = order.email
    personal_download_items = []
    for item in order.items.all():
        if item.content_type.model not in {"photo", "video"}:
            continue
        token_obj = ensure_personal_download_token(item)
        personal_download_items.append(
            {
                "title": getattr(item.product, "title", f"Digital item {item.object_id}"),
                "download_url": _build_personal_download_url(request, token_obj),
            }
        )

    context = {
        'order': order,
        'contact_email': settings.DEFAULT_FROM_EMAIL,
        'personal_terms_url': get_personal_licence_url(request=request),
        'personal_terms_summary': get_personal_licence_summary(),
        'personal_download_items': personal_download_items,
        'profile_url': _build_profile_url(),
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
