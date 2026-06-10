import logging

from django.core.management.base import BaseCommand

from checkout.tracking import get_prodigi_sync_candidates, refresh_order_from_prodigi

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Poll recent Prodigi-backed physical orders and send shipping emails when needed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Look back this many days for candidate Prodigi orders. Defaults to 90.",
        )

    def handle(self, *args, **options):
        lookback_days = max(int(options["days"] or 0), 1)
        candidates = list(
            get_prodigi_sync_candidates(lookback_days=lookback_days).order_by("-date")
        )

        logger.info(
            "Starting scheduled Prodigi shipment sync fallback. candidate_count=%s lookback_days=%s",
            len(candidates),
            lookback_days,
        )
        self.stdout.write(
            f"Found {len(candidates)} candidate Prodigi order(s) in the last {lookback_days} day(s)."
        )

        refreshed_count = 0
        emailed_count = 0
        failed_count = 0

        for order in candidates:
            try:
                sync_result = refresh_order_from_prodigi(order, mark_polled=True)
                refreshed_count += 1
                if sync_result["email_sent"]:
                    emailed_count += 1

                logger.info(
                    "Prodigi shipment sync processed order_number=%s prodigi_order_id=%s old_status=%s new_status=%s email_sent=%s email_skip_reason=%s",
                    order.order_number,
                    order.prodigi_order_id,
                    sync_result["old_status"] or "",
                    sync_result["new_status"] or "",
                    sync_result["email_sent"],
                    sync_result["email_skipped_reason"] or "",
                )
            except Exception as exc:
                failed_count += 1
                logger.exception(
                    "Prodigi shipment sync failed for order_number=%s prodigi_order_id=%s",
                    order.order_number,
                    order.prodigi_order_id,
                )
                self.stderr.write(
                    self.style.WARNING(
                        f"Failed to sync order {order.order_number} ({order.prodigi_order_id}): {exc}"
                    )
                )

        summary = (
            f"Prodigi shipment sync complete. candidates={len(candidates)} "
            f"refreshed={refreshed_count} emailed={emailed_count} failed={failed_count}"
        )
        logger.info(summary)
        self.stdout.write(self.style.SUCCESS(summary))
