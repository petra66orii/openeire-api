import logging

from django.core.management.base import BaseCommand

from checkout.tracking import (
    get_prodigi_sync_candidates,
    get_prodigi_sync_debug_rows,
    refresh_order_from_prodigi,
)

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
        parser.add_argument(
            "--debug-candidates",
            action="store_true",
            help="Print recent order candidate/exclusion reasons and exit without syncing.",
        )

    def handle(self, *args, **options):
        lookback_days = max(int(options["days"] or 0), 1)
        debug_candidates = bool(options.get("debug_candidates"))
        candidates = list(
            get_prodigi_sync_candidates(lookback_days=lookback_days)
        )

        if debug_candidates:
            debug_rows = get_prodigi_sync_debug_rows(lookback_days=lookback_days)
            self.stdout.write(
                f"Candidate debug for recent orders in the last {lookback_days} day(s):"
            )
            if not debug_rows:
                self.stdout.write("  (no recent orders found)")
            for row in debug_rows:
                self.stdout.write(
                    "  - order_number={order_number} prodigi_order_id={prodigi_order_id} "
                    "status={prodigi_status} included={included} reasons={reasons} "
                    "tracking_email_sent_at={tracking_email_sent_at} "
                    "prodigi_last_polled_at={prodigi_last_polled_at}".format(
                        order_number=row["order_number"],
                        prodigi_order_id=row["prodigi_order_id"],
                        prodigi_status=row["prodigi_status"],
                        included=str(row["included"]).lower(),
                        reasons=",".join(row["reasons"]),
                        tracking_email_sent_at=row["tracking_email_sent_at"] or "n/a",
                        prodigi_last_polled_at=row["prodigi_last_polled_at"] or "n/a",
                    )
                )
            self.stdout.write("Debug mode only: no Prodigi sync was run.")
            return

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
