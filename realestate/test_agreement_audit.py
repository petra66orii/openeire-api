from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from openeire_api.admin import custom_admin_site
from .admin import RealEstateEnquiryAdmin
from .documents import (
    _build_booking_agreement_context, booking_agreement_missing_requirements,
    generate_booking_agreement_pdf, render_booking_agreement_markdown,
)
from .models import RealEstateEnquiry, RealEstateFinancialAdjustment, RealEstateBookingAgreementSnapshot
from .package_catalogue import REAL_ESTATE_PACKAGE_CATALOGUE, get_package
from .payments import calculate_realestate_deposit_amounts


class AgreementCorrectiveAuditTests(TestCase):
    def test_legacy_custom_placeholder_cannot_be_reissued(self):
        for structured in (False, True):
            with self.subTest(structured=structured):
                enquiry = self.enquiry(preferred_package="custom", quoted_price=Decimal("700"))
                context = {"package_name": "Custom - POA - Included photographs as specifically agreed"}
                if structured:
                    context.update(package_name="Custom", agreed_deliverables=["Included photographs as specifically agreed"])
                old = RealEstateBookingAgreementSnapshot.objects.create(
                    enquiry=enquiry, template_version="1.8", payment_arrangement=enquiry.payment_arrangement,
                    context=context, rendered_markdown="Original immutable agreement",
                )
                with self.assertRaisesRegex(ValueError, "approved deliverables"):
                    render_booking_agreement_markdown(enquiry, for_customer=True, create_new_version=True)
                self.assertEqual(enquiry.booking_agreement_snapshots.count(), 1)
                enquiry.agreed_scope = "12 aerial photographs of the north parcel"
                enquiry.save()
                revised = render_booking_agreement_markdown(enquiry, for_customer=True, create_new_version=True)
                self.assertIn(enquiry.agreed_scope, revised)
                old.refresh_from_db()
                self.assertEqual(old.rendered_markdown, "Original immutable agreement")

    def test_override_omits_catalogue_photograph_claims_from_email_and_snapshot(self):
        from .emails import build_realestate_email_context
        from django.template.loader import render_to_string

        enquiry = self.enquiry(agreed_scope="12 edited ground photographs only")
        text = render_booking_agreement_markdown(enquiry, for_customer=True)
        snapshot = enquiry.booking_agreement_snapshots.first()
        self.assertIn("12 edited ground photographs only", text)
        self.assertEqual(snapshot.context["included_photographs_label"], "")
        self.assertIsNone(snapshot.context["included_photograph_count"])
        # Clearing the live override must not revive catalogue claims for issued scope.
        enquiry.agreed_scope = ""
        enquiry.save()
        for extension in ("txt", "html"):
            body = render_to_string(f"emails/real_estate/booking_agreement.{extension}", build_realestate_email_context(enquiry))
            self.assertNotIn("30 professionally edited", body)
            self.assertNotIn("Included edited photographs:", body)
        self.assertIsNone(enquiry.get_included_photograph_count())

    def test_package_change_requires_scope_reconciliation(self):
        enquiry = self.enquiry()
        original = render_booking_agreement_markdown(enquiry, for_customer=True)
        enquiry.preferred_package = "premium"
        enquiry.save()
        with self.assertRaisesRegex(ValueError, "approved deliverables"):
            render_booking_agreement_markdown(enquiry, for_customer=True, create_new_version=True)
        self.assertEqual(enquiry.booking_agreement_snapshots.count(), 1)
        enquiry.agreed_scope = "35 edited ground photographs\nHosted 3D virtual tour"
        enquiry.save()
        revised = render_booking_agreement_markdown(enquiry, for_customer=True, create_new_version=True)
        self.assertIn("| Package name | Premium |", revised)
        self.assertIn("35 edited ground photographs", revised)
        self.assertNotIn("30 professionally edited", revised)
        self.assertEqual(enquiry.booking_agreement_snapshots.earliest("created_at").rendered_markdown, original)

    def test_package_change_does_not_reapprove_unchanged_override(self):
        enquiry = self.enquiry(agreed_scope="12 edited photographs")
        render_booking_agreement_markdown(enquiry, for_customer=True)
        enquiry.preferred_package = "premium"
        enquiry.save()
        with self.assertRaisesRegex(ValueError, "approved deliverables"):
            render_booking_agreement_markdown(enquiry, for_customer=True, create_new_version=True)

    def enquiry(self, **overrides):
        values = dict(
            name="Alex Murphy", email="alex@example.invalid", phone="091 555 0101",
            company_name="Example Property Agency", property_address="12 Example Road",
            county="Galway", property_type="Detached house", preferred_package="pro",
            quoted_price=Decimal(get_package("pro").price_eur),
            shoot_date=date(2026, 10, 12), payment_due_date=date(2026, 10, 12),
            expected_payment_method="cash", consent_to_contact=True,
        )
        values.update(overrides)
        return RealEstateEnquiry.objects.create(**values)

    def test_current_pro_name_price_and_explicit_deliverables(self):
        enquiry = self.enquiry()
        text = render_booking_agreement_markdown(enquiry, for_customer=True)
        self.assertIn("| Package name | Pro |", text)
        self.assertIn("| Total fee payable | €419.00 |", text)
        self.assertNotIn("399", text)
        self.assertNotIn("Pro -", text)
        for scope in ("30 professionally edited", "5-8 aerial drone stills", "2D measured floor plan",
                      "One combined 4K property film", "vertical 9:16 social-media video"):
            self.assertIn(scope, text)
        self.assertNotIn("DRAFT", text)

    def test_negotiated_quote_and_later_catalogue_change(self):
        enquiry = self.enquiry(quoted_price=Decimal("385.00"))
        calculate_realestate_deposit_amounts(enquiry)
        with patch.dict(REAL_ESTATE_PACKAGE_CATALOGUE, {"pro": replace(get_package("pro"), price_eur=999)}):
            text = render_booking_agreement_markdown(enquiry, for_customer=True)
        self.assertIn("| Total fee payable | €385.00 |", text)
        self.assertNotIn("999", text)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.quoted_total, Decimal("385.00"))

    def test_existing_monetary_snapshot_wins_over_later_quote_field_edits(self):
        enquiry = self.enquiry()
        calculate_realestate_deposit_amounts(enquiry)
        enquiry.quoted_price = Decimal("999")
        enquiry.save(update_fields=["quoted_price"])
        text = render_booking_agreement_markdown(enquiry, for_customer=True)
        self.assertIn("| Total fee payable | €419.00 |", text)
        self.assertNotIn("999", text)

    def test_issued_invoice_amounts_can_supply_missing_legacy_quote_fields(self):
        from .finance import ensure_invoices_for_arrangement

        enquiry = self.enquiry(payment_arrangement="full_upfront")
        ensure_invoices_for_arrangement(enquiry)
        RealEstateEnquiry.objects.filter(pk=enquiry.pk).update(
            quoted_price=None, quoted_total=None, quoted_subtotal=None,
        )
        enquiry.refresh_from_db()
        text = render_booking_agreement_markdown(enquiry, for_customer=True)
        self.assertIn("| Total fee payable | €419.00 |", text)

    def test_custom_quote_scope_and_terms_are_authoritative(self):
        enquiry = self.enquiry(
            preferred_package="custom", payment_arrangement="custom", quoted_price=Decimal("700"),
            custom_required_total=Decimal("700"),
            agreed_scope="12 aerial photographs of the north land parcel\nOne 90-second boundary film",
            custom_payment_terms="Acceptance confirms the booking. EUR 700 is due on 12 October 2026.",
        )
        text = render_booking_agreement_markdown(enquiry, for_customer=True)
        self.assertIn("| Total fee payable | €700.00 |", text)
        self.assertIn("- 12 aerial photographs of the north land parcel", text)
        self.assertIn("- One 90-second boundary film", text)
        self.assertNotIn("30 professionally edited", text)
        self.assertEqual(text.count(enquiry.custom_payment_terms), 1)
        self.assertNotIn("deposit", text.lower())

    def test_custom_customer_request_is_not_approved_scope(self):
        enquiry = self.enquiry(preferred_package="custom", message="I want everything photographed")
        with self.assertRaisesRegex(ValueError, "approved deliverables"):
            generate_booking_agreement_pdf(enquiry, for_customer=True)

    def test_required_fields_block_customer_output_but_allow_preview(self):
        for field, value in (("quoted_price", None), ("name", ""), ("email", ""),
                             ("property_address", ""), ("shoot_date", None),
                             ("preferred_package", "not_sure"), ("payment_due_date", None)):
            with self.subTest(field=field):
                enquiry = self.enquiry(**{field: value})
                with self.assertRaisesRegex(ValueError, "cannot be sent"):
                    generate_booking_agreement_pdf(enquiry, for_customer=True)
                self.assertFalse(enquiry.booking_agreement_snapshots.exists())
                preview = render_booking_agreement_markdown(enquiry, use_snapshot=False)
                self.assertIn("DRAFT / PREVIEW", preview)
                if field == "quoted_price":
                    self.assertIn("| Total fee payable | Not provided |", preview)
                    self.assertNotIn("419", preview)

    def test_unsaved_and_fabricated_references_block_customer_output(self):
        for pk in (None, 99999):
            enquiry = RealEstateEnquiry(id=pk, preferred_package="pro")
            with self.assertRaisesRegex(ValueError, "persisted booking reference"):
                generate_booking_agreement_pdf(enquiry, for_customer=True)
        preview = render_booking_agreement_markdown(RealEstateEnquiry(preferred_package="pro"), use_snapshot=False)
        self.assertIn("RE-DRAFT", preview)

    def test_shoot_day_cannot_have_a_conflicting_due_date(self):
        enquiry = self.enquiry(payment_arrangement="full_on_shoot_day", payment_due_date=date(2026, 10, 14))
        self.assertIn("payment due date matching the shoot date", booking_agreement_missing_requirements(enquiry, for_customer=True))

    def test_signed_scope_survives_catalogue_change_and_explicit_new_version(self):
        enquiry = self.enquiry()
        original = render_booking_agreement_markdown(enquiry, for_customer=True)
        with patch.dict(REAL_ESTATE_PACKAGE_CATALOGUE, {"pro": replace(get_package("pro"), included_photographs=99)}):
            self.assertEqual(render_booking_agreement_markdown(enquiry), original)
            revised = render_booking_agreement_markdown(enquiry, create_new_version=True, for_customer=True)
        self.assertIn("30 professionally edited", revised)
        self.assertNotIn("99 professionally edited", revised)
        self.assertEqual(enquiry.booking_agreement_snapshots.earliest("created_at").rendered_markdown, original)

    def test_adjustments_and_travel_are_documented_without_catalogue_rates(self):
        enquiry = self.enquiry(quoted_price=Decimal("484"), add_ons=["travel_supplement"],
                               travel_supplement_amount=Decimal("65"), travel_details="Agreed round trip to Roscommon")
        calculate_realestate_deposit_amounts(enquiry)
        RealEstateFinancialAdjustment.objects.create(
            enquiry=enquiry, adjustment_type="negotiated_discount", amount=Decimal("20"),
            customer_description="Agreed discount", internal_reason="Approved negotiated discount",
            created_by=get_user_model().objects.create_user("adjuster"),
        )
        context = _build_booking_agreement_context(enquiry)
        self.assertEqual(context["quote_total"], "€484.00")
        self.assertEqual(context["total_required"], "€464.00")
        self.assertEqual(context["adjustment_total"], "€20.00")
        self.assertEqual(Decimal(context["deposit_amount"][1:]) + Decimal(context["balance_due"][1:]), Decimal("464"))
        self.assertEqual(context["travel_supplement_amount"], "€65.00")
        self.assertNotIn("0.50", context["add_ons_summary"])

    @override_settings(BUSINESS_DISPLAY_NAME="OpenÉire Studios")
    def test_all_arrangements_have_neutral_signing_and_consistent_brand(self):
        from reportlab.platypus import Table
        from openeire_api.pdf_markdown import render_markdown_to_flowables

        for arrangement in RealEstateEnquiry.PaymentArrangement.values:
            enquiry = self.enquiry(payment_arrangement=arrangement,
                custom_required_total=Decimal("419") if arrangement == "custom" else None,
                custom_payment_terms="Acceptance confirms booking. Payment due on shoot day." if arrangement == "custom" else "")
            text = render_booking_agreement_markdown(enquiry, for_customer=True)
            self.assertIn("By signing or otherwise formally accepting", text)
            self.assertIn("Issued for and on behalf of OpenÉire Studios", text)
            self.assertIn("| Client acceptance details | Completion |", text)
            self.assertNotIn("electronically", text)
            self.assertNotIn("Handwritten", text)
            self.assertNotIn("OpenEire", text)
            self.assertIn("OpenÉire Studios will correct an obvious technical defect", text)
            self.assertIn("production or editing charges where applicable", text)
            if arrangement != "deposit_then_balance":
                self.assertNotIn("deposit", text.lower())
                self.assertNotIn("balance", text.lower())
            self.assertTrue(generate_booking_agreement_pdf(enquiry, for_customer=True).startswith(b"%PDF"))
            tables = [item for item in render_markdown_to_flowables(text) if isinstance(item, Table)]
            payment_table = next(table for table in tables if table._cellvalues[0][0].getPlainText() == "Package and payment details")
            labels = [row[0].getPlainText() for row in payment_table._cellvalues]
            for label in ("Package name", "VAT", "Total fee payable", "Payment arrangement",
                          "Payment due date", "Expected payment method"):
                self.assertIn(label, labels)
            self.assertIn("Deposit required" if arrangement == "deposit_then_balance" else
                          "Approved custom payment schedule" if arrangement == "custom" else "Full payment due", labels)

    @patch("realestate.admin.send_templated_email")
    def test_admin_blocks_missing_price_and_sends_matching_agreement_context(self, send):
        admin = RealEstateEnquiryAdmin(RealEstateEnquiry, custom_admin_site)
        admin.message_user = Mock()
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("audit", "audit@example.invalid", "test-password")
        enquiry = self.enquiry(quoted_price=None)
        admin.send_booking_agreement_email(request, RealEstateEnquiry.objects.filter(pk=enquiry.pk))
        send.assert_not_called()
        enquiry.quoted_price = Decimal("385")
        enquiry.save()
        admin.send_booking_agreement_email(request, RealEstateEnquiry.objects.filter(pk=enquiry.pk))
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["context"]["package_name"], "Pro")
        self.assertEqual(send.call_args.kwargs["context"]["total_required"], "€385.00")
