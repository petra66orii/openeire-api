import logging
import secrets
from django.conf import settings
from rest_framework import permissions
from .models import GalleryAccess

logger = logging.getLogger(__name__)

class IsDigitalGalleryAuthorized(permissions.BasePermission):
    """
    Allows access only if a valid 'X-Gallery-Access-Token' header is present
    and matches an active GalleryAccess record.
    """
    message = "Digital Gallery Access Required. Please request an access code."

    def has_permission(self, request, view):
        # 1. Get the token from the header
        token = request.headers.get('X-Gallery-Access-Token')
        
        if not token:
            return False

        # 2. Check if the token exists and is valid
        try:
            access_record = GalleryAccess.objects.get(access_code=token)
            return access_record.is_valid
        except GalleryAccess.DoesNotExist:
            return False


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
