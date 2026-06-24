from django.conf import settings
from django.core import checks

from checkout.alerts import get_fulfilment_alert_recipients


@checks.register(checks.Tags.security)
def check_fulfilment_alert_recipients(app_configs, **kwargs):
    if (
        getattr(settings, "DEBUG", False)
        or getattr(settings, "IS_TEST_ENV", False)
        or get_fulfilment_alert_recipients()
    ):
        return []

    return [
        checks.Error(
            "Paid-order fulfilment alerts have no configured recipients.",
            hint=(
                "Set FULFILMENT_ALERT_RECIPIENTS to one or more monitored "
                "operations email addresses."
            ),
            id="checkout.E001",
        )
    ]
