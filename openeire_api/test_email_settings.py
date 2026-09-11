"""Regression checks for local SMTP isolation and the production relay."""
import json
import os
from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase


class EmailSettingsTests(SimpleTestCase):
    def load_settings(self, debug):
        environment = {
            **os.environ,
            "DEBUG": str(debug),
            "DJANGO_SETTINGS_MODULE": "openeire_api.settings_test",
            "EMAIL_HOST_USER": "production-user-sentinel",
            "EMAIL_HOST_PASSWORD": "production-password-sentinel",
            "DEFAULT_FROM_EMAIL": "studio@example.com",
        }
        script = """
import json
from django.conf import settings
keys = ('EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS',
        'EMAIL_USE_SSL', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL')
print(json.dumps({key: getattr(settings, key) for key in keys}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment,
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def test_development_ignores_production_credentials(self):
        settings = self.load_settings(True)
        self.assertEqual(settings["EMAIL_BACKEND"], "django.core.mail.backends.smtp.EmailBackend")
        self.assertEqual(settings["EMAIL_HOST"], "localhost")
        self.assertEqual(settings["EMAIL_PORT"], 1025)
        self.assertFalse(settings["EMAIL_USE_TLS"])
        self.assertFalse(settings["EMAIL_USE_SSL"])
        self.assertEqual(settings["EMAIL_HOST_USER"], "")
        self.assertEqual(settings["EMAIL_HOST_PASSWORD"], "")

    def test_production_relay_and_sender_identity_are_preserved(self):
        production = self.load_settings(False)
        self.assertEqual(production["EMAIL_BACKEND"], "django.core.mail.backends.smtp.EmailBackend")
        self.assertEqual(production["EMAIL_HOST"], "smtp-relay.brevo.com")
        self.assertEqual(production["EMAIL_PORT"], 587)
        self.assertTrue(production["EMAIL_USE_TLS"])
        self.assertFalse(production["EMAIL_USE_SSL"])
        self.assertEqual(production["EMAIL_HOST_USER"], "production-user-sentinel")
        self.assertEqual(production["EMAIL_HOST_PASSWORD"], "production-password-sentinel")
        self.assertEqual(production["DEFAULT_FROM_EMAIL"], self.load_settings(True)["DEFAULT_FROM_EMAIL"])
