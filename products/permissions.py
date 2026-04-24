import logging
import secrets
from django.conf import settings
from rest_framework import permissions

logger = logging.getLogger(__name__)

class IsDigitalGalleryAuthorized(permissions.BasePermission):
    """
    Allows access only to authenticated users whose account has active digital
    gallery access.
    """
    message = "Digital gallery access is linked to your account. Please sign in and verify your access code."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        profile = getattr(user, "userprofile", None)
        if not profile:
            return False
        return bool(profile.has_digital_gallery_access)


class IsAIWorkerAuthorized(permissions.BasePermission):
    """
    Allows access only if a valid AI worker token is provided.
    Optionally enforces an IP allowlist via settings.AI_WORKER_IP_ALLOWLIST.
    """
    message = "Unauthorized"

    def has_permission(self, request, view):
        expected_secret = getattr(settings, 'AI_WORKER_SECRET', None)
        if not expected_secret or expected_secret == 'secret':
            logger.error("AI_WORKER_SECRET missing or insecure; denying access.")
            return False

        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return False

        token = auth_header[len('Bearer '):].strip()
        if not token:
            return False

        if not secrets.compare_digest(token, expected_secret):
            return False

        allowlist = getattr(settings, 'AI_WORKER_IP_ALLOWLIST', None)
        if allowlist:
            client_ip = request.META.get('REMOTE_ADDR', '')
            trusted_proxies = getattr(settings, 'AI_WORKER_TRUSTED_PROXY_IPS', None)
            if trusted_proxies and client_ip in trusted_proxies:
                forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
                if forwarded_for:
                    client_ip = forwarded_for.split(',')[0].strip()
            if client_ip not in allowlist:
                return False

        return True


class IsStaffUser(permissions.BasePermission):
    """
    Allows access only to authenticated staff users.
    """
    message = "Staff access required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_staff)
