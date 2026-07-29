from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import RealEstateEnquiry, RealEstateTimelineEvent


@receiver(post_save, sender=RealEstateEnquiry)
def record_completed_shoot_once(sender, instance, **kwargs):
    if instance.status != RealEstateEnquiry.Status.COMPLETED:
        return
    actor = getattr(instance, "_completion_actor", None)
    RealEstateTimelineEvent.objects.get_or_create(
        enquiry=instance,
        event_type=RealEstateTimelineEvent.EventType.SHOOT_COMPLETED,
        defaults={
            "status": RealEstateTimelineEvent.EventStatus.COMPLETED,
            "actor_type": (
                RealEstateTimelineEvent.ActorType.ADMIN
                if actor
                else RealEstateTimelineEvent.ActorType.SYSTEM
            ),
            "title": "Shoot completed",
            "created_by": actor,
        },
    )
