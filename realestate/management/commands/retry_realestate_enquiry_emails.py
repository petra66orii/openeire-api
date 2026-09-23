from django.core.management.base import BaseCommand
from django.db.models import Q

from realestate.enquiry_notifications import send_enquiry_notifications
from realestate.models import RealEstateEnquiry


class Command(BaseCommand):
    help = "Retry unsent enquiry notifications and client confirmations."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise ValueError("--limit must be positive")
        pending = RealEstateEnquiry.objects.filter(
            Q(internal_notification_sent_at__isnull=True)
            | Q(client_confirmation_sent_at__isnull=True)
        ).order_by("created_at").values_list("pk", flat=True)[: options["limit"]]
        for enquiry_id in pending:
            result = send_enquiry_notifications(enquiry_id)
            self.stdout.write(f"Enquiry {enquiry_id}: {result}")
