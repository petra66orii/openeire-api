from django.core.management.base import BaseCommand

from home.brevo import (
    brevo_newsletter_configured,
    brevo_newsletter_enabled,
    sync_subscriber_to_brevo,
)
from home.models import NewsletterSubscriber


class Command(BaseCommand):
    help = "Sync local newsletter subscribers to Brevo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview subscribers that would be synced without calling Brevo.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        queryset = NewsletterSubscriber.objects.exclude(brevo_sync_status="synced").order_by("created_at")
        subscribers = list(queryset)

        self.stdout.write(f"Found {len(subscribers)} subscriber(s) pending Brevo sync.")
        if not brevo_newsletter_enabled():
            self.stdout.write("Brevo sync is disabled; no sync attempted.")
            return
        if not brevo_newsletter_configured():
            self.stdout.write("Brevo sync is enabled but not fully configured; no sync attempted.")
            return

        synced_count = 0
        failed_count = 0

        for subscriber in subscribers:
            if dry_run:
                self.stdout.write(f"Would sync {subscriber.email}")
                continue
            try:
                synced, status_label = sync_subscriber_to_brevo(subscriber, allow_disabled=False)
            except RuntimeError as exc:
                failed_count += 1
                self.stderr.write(f"Failed to sync {subscriber.email}: {exc}")
                continue
            if synced or status_label == "synced":
                synced_count += 1
            else:
                failed_count += 1

        if dry_run:
            self.stdout.write("Dry run complete.")
            return

        self.stdout.write(
            f"Brevo sync complete. synced={synced_count} failed={failed_count}"
        )
