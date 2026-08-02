from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from whitenoise.storage import CompressedManifestStaticFilesStorage


UPLOADER_STATIC_PATH = "realestate/js/delivery_multipart_uploader.mjs"


class DeliveryUploaderStaticFilesTests(SimpleTestCase):
    def test_uploader_module_is_discoverable_from_the_realestate_app(self):
        discovered_path = finders.find(UPLOADER_STATIC_PATH)
        app_static_root = (
            Path(settings.BASE_DIR) / "realestate" / "static"
        ).resolve()

        self.assertIsNotNone(discovered_path)
        self.assertTrue(Path(discovered_path).is_file())
        self.assertTrue(Path(discovered_path).resolve().is_relative_to(app_static_root))
        self.assertFalse(
            (Path(settings.BASE_DIR) / "static" / UPLOADER_STATIC_PATH).exists()
        )

    def test_uploader_module_is_resolvable_from_the_production_manifest(self):
        production_storages = {
            **settings.STORAGES,
            "staticfiles": {
                "BACKEND": (
                    "whitenoise.storage.CompressedManifestStaticFilesStorage"
                )
            },
        }

        with TemporaryDirectory(dir=settings.BASE_DIR) as static_root:
            with override_settings(
                DEBUG=False,
                STATIC_ROOT=static_root,
                STORAGES=production_storages,
            ):
                call_command("collectstatic", interactive=False, verbosity=0)

                manifest_storage = CompressedManifestStaticFilesStorage(
                    location=static_root,
                    base_url=settings.STATIC_URL,
                )
                manifest_name = manifest_storage.stored_name(UPLOADER_STATIC_PATH)

                self.assertNotEqual(manifest_name, UPLOADER_STATIC_PATH)
                self.assertTrue((Path(static_root) / manifest_name).is_file())
                self.assertEqual(
                    manifest_storage.url(UPLOADER_STATIC_PATH),
                    f"{settings.STATIC_URL}{manifest_name}",
                )
