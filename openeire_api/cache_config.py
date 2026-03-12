from django.core.exceptions import ImproperlyConfigured


def env_bool(raw_value, default=False):
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def infer_runtime_env(app_env, render_environment, debug, running_tests):
    if app_env:
        return app_env
    if running_tests:
        return "test"
    if render_environment:
        return render_environment
    if debug:
        return "development"
    return "production"


def _coerce_timeout(raw_value, default):
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def build_cache_settings(
    *,
    cache_redis_url,
    cache_key_prefix,
    cache_redis_connect_timeout_seconds,
    cache_redis_socket_timeout_seconds,
    throttle_cache_alias,
    require_shared_throttle_cache,
):
    if cache_redis_url:
        redis_cache_base = {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": cache_redis_url,
            "KEY_PREFIX": cache_key_prefix,
            "OPTIONS": {
                # Keep throttle latency bounded during Redis degradation.
                "socket_connect_timeout": _coerce_timeout(cache_redis_connect_timeout_seconds, 0.5),
                "socket_timeout": _coerce_timeout(cache_redis_socket_timeout_seconds, 0.5),
            },
        }
        return {
            "default": {
                **redis_cache_base,
            },
            throttle_cache_alias: {
                **redis_cache_base,
                # Throttle keys should control their own expiry by rate window.
                "TIMEOUT": None,
            },
        }

    if require_shared_throttle_cache:
        raise ImproperlyConfigured(
            "Shared throttle cache is required but CACHE_REDIS_URL/REDIS_URL is not set."
        )

    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "openeire-default-cache",
        },
        throttle_cache_alias: {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "openeire-throttle-cache",
            "TIMEOUT": None,
        },
    }
