"""Operational documents, deliberately independent of booking/payment state."""
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def authorisation_token():
    return secrets.token_urlsafe(32)


class PropertyAuthorisation(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent / awaiting authorisation"
        ELECTRONIC = "electronic", "Accepted electronically"
        HANDWRITTEN = "handwritten", "Handwritten copy received"

    enquiry = models.ForeignKey("RealEstateEnquiry", on_delete=models.PROTECT, related_name="property_authorisations")
    location = models.TextField(help_text="Exact property, parcel or locations covered by this authorisation only.")
    template_version = models.CharField(max_length=16, default="1.0", editable=False)
    token = models.CharField(max_length=64, default=authorisation_token, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    issued_snapshot = models.JSONField(default=dict, editable=False)
    accepted_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    issued_pdf = models.BinaryField(editable=False)
    accepted_pdf = models.BinaryField(null=True, editable=False)
    recipient_email = models.EmailField()
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replacement")
    accepted_name = models.CharField(max_length=255, blank=True)
    accepted_capacity = models.CharField(max_length=255, blank=True)
    accepted_email = models.EmailField(blank=True)
    acceptance_method = models.CharField(max_length=16, blank=True, choices=[("electronic", "Electronic"), ("handwritten", "Handwritten")])
    accepted_at = models.DateTimeField(null=True, blank=True)
    received_on = models.DateField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="property_authorisations_recorded")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="property_authorisations_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Property access & shoot authorisation"

    @property
    def display_status(self):
        if self.superseded_at:
            return "Superseded / reissued"
        if self.revoked_at:
            return "Revoked"
        return self.get_status_display()

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            protected = ["enquiry_id", "location", "template_version", "token", "issued_snapshot", "issued_pdf", "expires_at", "recipient_email", "supersedes_id", "created_by_id"]
            if old.accepted_at:
                protected += ["accepted_snapshot", "accepted_pdf", "accepted_name", "accepted_capacity", "accepted_email", "acceptance_method", "accepted_at", "received_on", "recorded_by_id", "status"]
            if any(getattr(old, key) != getattr(self, key) for key in protected):
                raise ValidationError("Issued and accepted document content is immutable. Reissue instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Authorisations are retained for audit; revoke or reissue instead.")

    def __str__(self):
        return f"RE-{self.enquiry_id} authorisation v{self.template_version}: {self.display_status}"


class PropertyScopeCheck(models.Model):
    class Outcome(models.TextChoices):
        COMPLETE = "complete", "No outstanding agreed capture identified"
        OUTSTANDING = "outstanding", "Outstanding agreed capture noted"
        NO_SPECIFIC = "no_specific", "No additional specific requirements identified"

    enquiry = models.ForeignKey("RealEstateEnquiry", on_delete=models.PROTECT, related_name="property_scope_checks")
    location = models.TextField()
    person_confirming = models.CharField(max_length=255, help_text="Name, or explicitly record that no representative was present/available.")
    capacity = models.CharField(max_length=255, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    all_agreed_completed = models.BooleanField(default=True)
    outstanding_capture = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def clean(self):
        if (not self.all_agreed_completed or self.outcome == self.Outcome.OUTSTANDING) and not self.outstanding_capture.strip():
            raise ValidationError({"outstanding_capture": "Describe the outstanding agreed capture."})
        if self.all_agreed_completed and (self.outcome == self.Outcome.OUTSTANDING or self.outstanding_capture.strip()):
            raise ValidationError({"all_agreed_completed": "Outstanding capture cannot be marked fully completed."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Final scope checks are append-only. Record a new check to correct one.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Final scope checks are retained for audit.")
