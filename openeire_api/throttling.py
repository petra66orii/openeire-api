import logging

from django.conf import settings
from django.core.cache import caches
from rest_framework import exceptions
from rest_framework.throttling import ScopedRateThrottle

logger = logging.getLogger(__name__)


class SharedScopedRateThrottle(ScopedRateThrottle):
    """
    Use a dedicated cache alias so throttle state can be shared globally
    (for example via Redis) across workers/instances.
    """

    cache = caches[getattr(settings, "THROTTLE_CACHE_ALIAS", "throttle")]

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            fail_open = getattr(settings, "THROTTLE_FAIL_OPEN", False)
            if fail_open:
                logger.warning(
                    "Throttle cache unavailable; allowing request due to fail-open policy.",
                    exc_info=True,
                )
                return True

            logger.warning(
                "Throttle cache unavailable; rejecting request due to fail-closed policy.",
                exc_info=True,
            )
            raise exceptions.Throttled(
                detail="Request was throttled due to temporary throttling backend issue."
            )

    def wait(self):
        try:
            return super().wait()
        except Exception:
            return None
