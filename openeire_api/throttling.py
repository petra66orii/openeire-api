import logging

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError
from rest_framework import exceptions
from rest_framework.throttling import ScopedRateThrottle

logger = logging.getLogger(__name__)

try:
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - redis may not be installed in some local test envs.
    RedisError = None


_THROTTLE_CACHE_EXCEPTIONS = (InvalidCacheBackendError,) + ((RedisError,) if RedisError else ())


class SharedScopedRateThrottle(ScopedRateThrottle):
    """
    Use a dedicated cache alias so throttle state can be shared globally
    (for example via Redis) across workers/instances.
    """

    def _resolve_cache(self):
        return caches[getattr(settings, "THROTTLE_CACHE_ALIAS", "throttle")]

    def allow_request(self, request, view):
        try:
            self.cache = self._resolve_cache()
            return super().allow_request(request, view)
        except _THROTTLE_CACHE_EXCEPTIONS:
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
                detail="Rate limiting backend is temporarily unavailable. Please retry shortly."
            )

    def wait(self):
        try:
            self.cache = self._resolve_cache()
            return super().wait()
        except _THROTTLE_CACHE_EXCEPTIONS:
            return None
