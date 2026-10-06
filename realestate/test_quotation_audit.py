import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from realestate.models import (
    RealEstateEnquiry,
    RealEstateQuotationSnapshot,
    RealEstateTimelineEvent,
)
from realestate.package_catalogue import (
    CURRENT_CATALOGUE_VERSION,
    LEGACY_CATALOGUE_VERSION,
)


class LegacyQuotationAuditCommandTests(TestCase):
    def enquiry(self, **overrides):
        values = {
            "name": "Audit Client",
            "email": "audit@example.com",
            "phone": "123",
            "client_type": RealEstateEnquiry.ClientType.PRIVATE_SELLER,
            "property_address": "1 Audit Road, Galway",
            "county": "Galway",
            "property_type": "house",
            "preferred_package": RealEstateEnquiry.PreferredPackage.STARTER,
            "catalogue_version": LEGACY_CATALOGUE_VERSION,
            "quoted_price": Decimal("259.00"),
            "quoted_total": Decimal("259.00"),
            "status": RealEstateEnquiry.Status.QUOTED,
            "consent_to_contact": True,
        }
        values.update(overrides)
        return RealEstateEnquiry.objects.create(**values)

    def quote_event(self, enquiry, **overrides):
        values = {
            "enquiry": enquiry,
            "event_type": RealEstateTimelineEvent.EventType.QUOTE_SENT,
            "status": RealEstateTimelineEvent.EventStatus.SENT,
            "title": "Quote email sent",
            "email_template": "quote",
            "recipient_email": enquiry.email,
        }
        values.update(overrides)
        return RealEstateTimelineEvent.objects.create(**values)

    def run_audit(self):
        output = StringIO()
        call_command("audit_legacy_quotations", "--format=json", stdout=output)
        return json.loads(output.getvalue())

    def test_unresolved_pre_snapshot_quote_requires_review(self):
        enquiry = self.enquiry()
        event = self.quote_event(enquiry)

        payload = self.run_audit()

        self.assertEqual(payload["counts"], {"SAFE": 0, "REVIEW": 1, "CLOSED": 0})
        result = payload["results"][0]
        self.assertEqual(result["quote_id"], f"timeline:{event.pk}")
        self.assertFalse(result["quotation_body_snapshot_exists"])
        self.assertTrue(result["manual_review_required"])
        self.assertIn("no quotation-specific expiry", " ".join(result["review_reasons"]))

    def test_matching_snapshot_is_detected_but_no_expiry_still_requires_review(self):
        enquiry = self.enquiry()
        event = self.quote_event(enquiry)
        snapshot = RealEstateQuotationSnapshot.objects.create(
            enquiry=enquiry,
            catalogue_version=LEGACY_CATALOGUE_VERSION,
            subject="Quote",
            recipient_email=enquiry.email,
            context={"package_name": "Starter"},
            rendered_text="Frozen quote",
            rendered_html="<p>Frozen quote</p>",
        )
        RealEstateQuotationSnapshot.objects.filter(pk=snapshot.pk).update(
            issued_at=event.created_at - timedelta(seconds=2)
        )

        payload = self.run_audit()

        self.assertEqual(payload["counts"], {"SAFE": 0, "REVIEW": 1, "CLOSED": 0})
        result = payload["results"][0]
        self.assertTrue(result["quotation_body_snapshot_exists"])
        self.assertEqual(result["snapshot_id"], snapshot.pk)
        self.assertNotIn(
            "no immutable quotation-body snapshot matches this issue event",
            result["review_reasons"],
        )

    def test_earlier_repeat_quote_is_flagged_as_implicitly_superseded(self):
        enquiry = self.enquiry()
        first = self.quote_event(enquiry)
        second = self.quote_event(enquiry)
        RealEstateTimelineEvent.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        RealEstateTimelineEvent.objects.filter(pk=second.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )

        results = self.run_audit()["results"]

        first_result = next(row for row in results if row["timeline_event_id"] == first.pk)
        self.assertTrue(first_result["appears_superseded"])
        self.assertEqual(first_result["later_quote_event_ids"], [second.pk])
        self.assertIn("not explicitly superseded", " ".join(first_result["review_reasons"]))

    def test_closed_or_converted_quote_is_historical_only(self):
        enquiry = self.enquiry(
            status=RealEstateEnquiry.Status.CLOSED,
            booking_agreement_received=True,
        )
        self.quote_event(enquiry)

        payload = self.run_audit()

        self.assertEqual(payload["counts"], {"SAFE": 0, "REVIEW": 0, "CLOSED": 1})
        result = payload["results"][0]
        self.assertTrue(result["accepted_or_converted"])
        self.assertFalse(result["manual_review_required"])

    def test_custom_scope_is_reported_for_manual_review(self):
        enquiry = self.enquiry(
            preferred_package=RealEstateEnquiry.PreferredPackage.CUSTOM,
            quoted_price=Decimal("700.00"),
            quoted_total=Decimal("700.00"),
            agreed_scope="Photography\nDrone coverage",
            agreed_photograph_count=40,
        )
        self.quote_event(enquiry)

        result = self.run_audit()["results"][0]

        self.assertEqual(result["classification"], "REVIEW")
        reasons = " ".join(result["review_reasons"])
        self.assertIn("manually customised agreed scope", reasons)
        self.assertIn("explicit agreed photograph count", reasons)
        self.assertIn("Custom / POA", reasons)

    def test_current_catalogue_quote_is_outside_the_legacy_population(self):
        enquiry = self.enquiry(catalogue_version=CURRENT_CATALOGUE_VERSION)
        self.quote_event(enquiry)
        before = {
            "enquiries": RealEstateEnquiry.objects.count(),
            "events": RealEstateTimelineEvent.objects.count(),
            "snapshots": RealEstateQuotationSnapshot.objects.count(),
        }

        payload = self.run_audit()

        self.assertEqual(payload["results"], [])
        self.assertEqual(
            before,
            {
                "enquiries": RealEstateEnquiry.objects.count(),
                "events": RealEstateTimelineEvent.objects.count(),
                "snapshots": RealEstateQuotationSnapshot.objects.count(),
            },
        )

    def test_unknown_historical_catalogue_does_not_fall_back_to_current_prices(self):
        enquiry = self.enquiry(catalogue_version="residential_2026_08")
        self.quote_event(enquiry)

        result = self.run_audit()["results"][0]

        self.assertEqual(result["classification"], "REVIEW")
        self.assertIsNone(result["package_base_price"])
        self.assertIn(
            "stored catalogue version is not defined by this release",
            result["review_reasons"],
        )
