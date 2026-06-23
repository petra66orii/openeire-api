from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from checkout.models import CheckoutAttempt


class Command(BaseCommand):
    help = "Delete abandoned checkout attempts after the configured retention period."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override CHECKOUT_ATTEMPT_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the number of matching attempts without deleting them.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = int(getattr(settings, "CHECKOUT_ATTEMPT_RETENTION_DAYS", 30))
        if days < 1:
            raise CommandError("Retention days must be at least 1.")

        cutoff = timezone.now() - timedelta(days=days)
        attempts = CheckoutAttempt.objects.filter(
            order__isnull=True,
            created_at__lt=cutoff,
        )
        count = attempts.count()
        if options["dry_run"]:
            self.stdout.write(
                f"Would delete {count} abandoned checkout attempt(s) older than {days} day(s)."
            )
            return

        attempts.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {count} abandoned checkout attempt(s) older than {days} day(s)."
            )
        )
