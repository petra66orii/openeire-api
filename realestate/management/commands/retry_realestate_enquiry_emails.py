from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from realestate.enquiry_notifications import send_enquiry_notifications
from realestate.models import RealEstateEnquiry


class Command(BaseCommand):
    help = "Retry unsent enquiry notifications and client confirmations."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--fail-on-error",
            action="store_true",
            help="Exit non-zero when any notification send fails.",
        )

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise ValueError("--limit must be positive")
        pending = RealEstateEnquiry.objects.filter(
            Q(internal_notification_sent_at__isnull=True)
            | Q(client_confirmation_sent_at__isnull=True)
        ).order_by(
            F("enquiry_email_last_attempt_at").asc(nulls_first=True), "created_at"
        ).values_list("pk", flat=True)[: options["limit"]]
        totals = {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}
        for enquiry_id in pending:
            result = send_enquiry_notifications(enquiry_id)
            totals["processed"] += 1
            for outcome in result.values():
                if outcome == "sent":
                    totals["sent"] += 1
                elif outcome == "failed":
                    totals["failed"] += 1
                else:
                    totals["skipped"] += 1
            self.stdout.write(f"Enquiry {enquiry_id}: {result}")
        summary = " ".join(f"{key}={value}" for key, value in totals.items())
        self.stdout.write(f"Summary: {summary}")
        if options["fail_on_error"] and totals["failed"]:
            raise CommandError(f"Enquiry email retries failed: {summary}")
