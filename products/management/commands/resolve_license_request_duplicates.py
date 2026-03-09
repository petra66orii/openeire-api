from collections import defaultdict

from django.core.management.base import BaseCommand

from products.models import LicenseRequest


STATUS_PRIORITY = {
    "DELIVERED": 9,
    "PAID": 8,
    "PAYMENT_PENDING": 7,
    "APPROVED": 6,
    "NEEDS_INFO": 5,
    "SUBMITTED": 4,
    "DRAFT": 3,
    "EXPIRED": 2,
    "REVOKED": 2,
    "REJECTED": 1,
}


def normalize_email(value):
    if value is None:
        return ""
    return str(value).strip().lower()


class Command(BaseCommand):
    help = (
        "Find (and optionally resolve) duplicate active LicenseRequests that "
        "differ only by email casing/whitespace."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply resolution by rejecting duplicates (keeps the best candidate).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        rows = LicenseRequest.objects.exclude(status="REJECTED").values(
            "id",
            "email",
            "content_type_id",
            "object_id",
            "status",
            "created_at",
        )

        grouped = defaultdict(list)
        for row in rows:
            key = (
                row["content_type_id"],
                row["object_id"],
                normalize_email(row["email"]),
            )
            grouped[key].append(row)

        duplicates = {k: v for k, v in grouped.items() if len(v) > 1}
        if not duplicates:
            self.stdout.write(self.style.SUCCESS("No duplicates found."))
            return

        total_dupes = sum(len(v) - 1 for v in duplicates.values())
        self.stdout.write(
            self.style.WARNING(
                f"Found {len(duplicates)} duplicate group(s), {total_dupes} extra record(s)."
            )
        )

        for (ct_id, obj_id, norm_email), rows in duplicates.items():
            self.stdout.write(
                f"\n- content_type_id={ct_id} object_id={obj_id} email={norm_email}"
            )
            for row in sorted(rows, key=lambda r: r["created_at"]):
                self.stdout.write(
                    f"  id={row['id']} status={row['status']} "
                    f"created_at={row['created_at']} email='{row['email']}'"
                )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "\nRun again with --apply to reject duplicates automatically."
                )
            )
            return

        rejected = 0
        for rows in duplicates.values():
            rows_sorted = sorted(
                rows,
                key=lambda r: (
                    -STATUS_PRIORITY.get(r["status"], 0),
                    r["created_at"],
                    r["id"],
                ),
            )
            keep_id = rows_sorted[0]["id"]
            for row in rows_sorted[1:]:
                obj = LicenseRequest.objects.get(id=row["id"])
                obj.status = "REJECTED"
                obj.save(update_fields=["status", "updated_at"])
                rejected += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"Kept id={keep_id}, rejected {len(rows_sorted) - 1} duplicate(s)."
                )
            )

        self.stdout.write(self.style.SUCCESS(f"Rejected {rejected} duplicate(s)."))
