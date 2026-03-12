from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from unittest.mock import patch

from openeire_api.cache_config import build_cache_settings, infer_runtime_env
from openeire_api.throttling import SharedScopedRateThrottle


class CacheConfigTests(SimpleTestCase):
    def test_missing_redis_url_raises_when_shared_cache_required(self):
        with self.assertRaises(ImproperlyConfigured):
            build_cache_settings(
                cache_redis_url=None,
                cache_key_prefix="openeire-api:production",
                cache_redis_connect_timeout_seconds=None,
                cache_redis_socket_timeout_seconds=None,
                throttle_cache_alias="throttle",
                require_shared_throttle_cache=True,
            )

    def test_fallback_locmem_when_shared_cache_not_required(self):
        caches = build_cache_settings(
            cache_redis_url=None,
            cache_key_prefix="openeire-api:development",
            cache_redis_connect_timeout_seconds=None,
            cache_redis_socket_timeout_seconds=None,
            throttle_cache_alias="throttle",
            require_shared_throttle_cache=False,
        )

        self.assertEqual(
            caches["throttle"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )

    def test_redis_cache_uses_environment_scoped_prefix(self):
        app_env = infer_runtime_env(
            app_env=None,
            render_environment="production",
            debug=False,
            running_tests=False,
        )
        caches = build_cache_settings(
            cache_redis_url="redis://redis.internal:6379/0",
            cache_key_prefix=f"openeire-api:{app_env}",
            cache_redis_connect_timeout_seconds=None,
            cache_redis_socket_timeout_seconds=None,
            throttle_cache_alias="throttle",
            require_shared_throttle_cache=True,
        )

        self.assertEqual(caches["throttle"]["KEY_PREFIX"], "openeire-api:production")
        self.assertEqual(caches["throttle"]["TIMEOUT"], None)
        self.assertEqual(caches["throttle"]["OPTIONS"]["socket_connect_timeout"], 0.5)
        self.assertEqual(caches["throttle"]["OPTIONS"]["socket_timeout"], 0.5)

    def test_infer_runtime_env_prefers_test_when_running_tests(self):
        app_env = infer_runtime_env(
            app_env=None,
            render_environment="production",
            debug=False,
            running_tests=True,
        )

        self.assertEqual(app_env, "test")

    def test_non_cache_errors_are_not_swallowed_by_fail_open_policy(self):
        throttle = SharedScopedRateThrottle()

        with self.settings(THROTTLE_FAIL_OPEN=True):
            with patch(
                "rest_framework.throttling.ScopedRateThrottle.allow_request",
                side_effect=ValueError("not a cache error"),
            ):
                with self.assertRaises(ValueError):
                    throttle.allow_request(object(), object())
