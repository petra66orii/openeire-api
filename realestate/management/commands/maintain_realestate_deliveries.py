from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from realestate.delivery_storage import abort_upload, delete_validated_object
from realestate.models import (
    RealEstateDeliverable,
    RealEstateDelivery,
    RealEstateDeliveryAccessEvent,
    RealEstateDeliveryUploadSession,
)


class Command(BaseCommand):
    help = "Expire portal deliveries and report or perform validated delivery cleanup."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Apply status changes, aborts and deletions. Default is dry-run.",
        )
        parser.add_argument(
            "--stale-upload-hours",
            type=int,
            default=24,
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        now = timezone.now()
        grace_days = int(
            getattr(settings, "REAL_ESTATE_DELIVERY_GRACE_DAYS", 60)
        )
        expired = RealEstateDelivery.objects.filter(
            status=RealEstateDelivery.Status.ACTIVE,
            expires_at__lte=now,
        )
        stale = RealEstateDeliveryUploadSession.objects.filter(
            status=RealEstateDeliveryUploadSession.Status.INITIATED,
            created_at__lte=now - timedelta(hours=options["stale_upload_hours"]),
        )
        orphan_candidates = RealEstateDeliveryUploadSession.objects.filter(
            status__in=(
                RealEstateDeliveryUploadSession.Status.COMPLETING,
                RealEstateDeliveryUploadSession.Status.COMPLETED,
                RealEstateDeliveryUploadSession.Status.FAILED,
            ),
            updated_at__lte=now - timedelta(hours=options["stale_upload_hours"]),
        ).select_related("delivery")
        orphaned = [
            session
            for session in orphan_candidates
            if not RealEstateDeliverable.objects.filter(
                object_key=session.object_key
            ).exists()
        ]
        eligible = RealEstateDeliverable.objects.filter(
            deleted_at__isnull=True,
            delivery__expires_at__lte=now - timedelta(days=grace_days),
        ).filter(
            Q(deletion_eligible_at__isnull=True)
            | Q(deletion_eligible_at__lte=now)
        )
        self.stdout.write(
            f"{'EXECUTE' if execute else 'DRY RUN'}: "
            f"{expired.count()} deliveries to expire, "
            f"{stale.count()} stale multipart uploads, "
            f"{len(orphaned)} completed/failed unreferenced uploads, "
            f"{eligible.count()} objects eligible for deletion."
        )
        if not execute:
            return
        expired.update(status=RealEstateDelivery.Status.EXPIRED)
        for session in stale.iterator():
            try:
                abort_upload(session)
                session.status = session.Status.ABORTED
                session.aborted_at = now
                session.save(update_fields=("status", "aborted_at", "updated_at"))
            except Exception as exc:
                session.status = session.Status.FAILED
                session.error_code = "cleanup_abort_failed"
                session.save(update_fields=("status", "error_code", "updated_at"))
                raise CommandError(
                    f"Multipart cleanup failed for session {session.pk}."
                ) from exc
        for session in orphaned:
            try:
                try:
                    abort_upload(session)
                except Exception:
                    # A completed multipart upload has no active upload to abort.
                    pass
                delete_validated_object(session.object_key)
                session.status = session.Status.FAILED
                session.error_code = "cleanup_orphan_removed"
                session.save(update_fields=("status", "error_code", "updated_at"))
                RealEstateDeliveryAccessEvent.objects.create(
                    delivery=session.delivery,
                    event_type=RealEstateDeliveryAccessEvent.EventType.CLEANUP_SUCCEEDED,
                    metadata={"reason": "orphan_upload_removed"},
                )
            except Exception as exc:
                RealEstateDeliveryAccessEvent.objects.create(
                    delivery=session.delivery,
                    event_type=RealEstateDeliveryAccessEvent.EventType.CLEANUP_FAILED,
                    metadata={"reason": "orphan_upload_cleanup_failed"},
                )
                raise CommandError(
                    f"Orphan cleanup failed for upload session {session.pk}."
                ) from exc
        for deliverable in eligible.select_related("delivery").iterator():
            if deliverable.replacements.filter(deleted_at__isnull=True).exists():
                continue
            try:
                delete_validated_object(deliverable.object_key)
                deliverable.deleted_at = now
                deliverable.is_active = False
                deliverable.save(
                    update_fields=("deleted_at", "is_active", "updated_at")
                )
                RealEstateDeliveryAccessEvent.objects.create(
                    delivery=deliverable.delivery,
                    deliverable=deliverable,
                    event_type=RealEstateDeliveryAccessEvent.EventType.CLEANUP_SUCCEEDED,
                    metadata={},
                )
            except Exception as exc:
                RealEstateDeliveryAccessEvent.objects.create(
                    delivery=deliverable.delivery,
                    deliverable=deliverable,
                    event_type=RealEstateDeliveryAccessEvent.EventType.CLEANUP_FAILED,
                    metadata={"reason": exc.__class__.__name__[:100]},
                )
                raise CommandError(
                    f"Object cleanup failed for deliverable {deliverable.pk}."
                ) from exc
