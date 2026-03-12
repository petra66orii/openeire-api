from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from openeire_api.cache_config import build_cache_settings, infer_runtime_env


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
