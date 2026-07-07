from .models import RealEstateTimelineEvent


def record_timeline_event(
    enquiry,
    event_type,
    *,
    status=RealEstateTimelineEvent.EventStatus.COMPLETED,
    actor_type=RealEstateTimelineEvent.ActorType.SYSTEM,
    title="",
    notes="",
    email_template="",
    recipient_email="",
    reference_url="",
    stripe_session_id="",
    created_by=None,
):
    if not title:
        title = RealEstateTimelineEvent.EventType(event_type).label

    return RealEstateTimelineEvent.objects.create(
        enquiry=enquiry,
        event_type=event_type,
        status=status,
        actor_type=actor_type,
        title=title,
        notes=notes,
        email_template=email_template,
        recipient_email=recipient_email,
        reference_url=reference_url,
        stripe_session_id=stripe_session_id,
        created_by=created_by,
    )
