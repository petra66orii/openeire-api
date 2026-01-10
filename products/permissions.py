from rest_framework import permissions
from .models import GalleryAccess

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