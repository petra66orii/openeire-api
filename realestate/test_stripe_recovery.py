from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import threading
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, connections, close_old_connections, transaction
from django.test import TransactionTestCase, RequestFactory, override_settings
from django.utils import timezone

from checkout.views import StripeWebhookView
from openeire_api.admin import custom_admin_site
from .admin import RealEstateInvoiceAdmin
from .finance import create_custom_instalment_invoice, void_local_realestate_invoice, can_release_realestate_delivery
from .models import RealEstateEnquiry, RealEstateInvoice
from .payments import calculate_realestate_deposit_amounts
from .stripe_invoices import create_stripe_invoice, send_stripe_invoice


class StatefulStripe:
    """Remote state and idempotency cache survive failures and database rollback."""
    def __init__(self, fail=None, after=False):
        self.api = MagicMock()
        self.fail, self.after, self.failed = fail, after, False
        self.cache, self.invoices, self.calls = {}, {}, []
        self.effects = {name: 0 for name in ("customer", "create", "item", "finalize", "send")}
        self.api.Customer.create.side_effect = lambda **kw: self.call("customer", kw)
        self.api.Invoice.create.side_effect = lambda **kw: self.call("create", kw)
        self.api.InvoiceItem.create.side_effect = lambda **kw: self.call("item", kw)
        self.api.Invoice.finalize_invoice.side_effect = lambda ident, **kw: self.call("finalize", kw, ident)
        self.api.Invoice.send_invoice.side_effect = lambda ident, **kw: self.call("send", kw, ident)
        self.api.Invoice.retrieve.side_effect = lambda ident: deepcopy(self.invoices[ident])

    def call(self, name, params, ident=None):
        if connection.in_atomic_block:
            raise AssertionError("Remote POST executed inside a database transaction")
        self.calls.append((name, deepcopy(params), ident))
        fail = name == self.fail and not self.failed
        if fail and not self.after:
            self.failed = True
            raise RuntimeError("Injected crash before execution")
        key = params["idempotency_key"]
        if key in self.cache:
            old_name, old_params, old_ident, result = self.cache[key]
            if (old_name, old_params, old_ident) != (name, params, ident):
                raise AssertionError("Idempotency request changed")
        else:
            self.effects[name] += 1
            if name == "customer":
                result = {"id": "cus_recovery"}
            elif name == "create":
                result = {
                    "id": f"in_recovery_{self.effects[name]}", "status": "draft",
                    "metadata": deepcopy(params["metadata"]), "currency": "eur",
                    "total": 0, "amount_due": 0, "livemode": False,
                    "latest_revision": None, "from_invoice": None,
                }
                self.invoices[result["id"]] = deepcopy(result)
            elif name == "item":
                remote = self.invoices[params["invoice"]]
                remote["total"] += params["amount"]
                remote["amount_due"] = remote["total"]
                result = {"id": f"ii_recovery_{self.effects[name]}"}
            else:
                self.invoices[ident]["status"] = "open"
                result = deepcopy(self.invoices[ident])
            self.cache[key] = (name, deepcopy(params), ident, deepcopy(result))
        if fail:
            self.failed = True
            raise RuntimeError("Injected lost response after execution")
        return deepcopy(result)


@override_settings(VAT_REGISTERED=False, STRIPE_SECRET_KEY="sk_test_mock_recovery")
class StripeRecoveryTests(TransactionTestCase):
    def setUp(self):
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Recovery fixture", email="fixture@example.invalid", property_address="Disposable property",
            quoted_price=Decimal("549"), payment_arrangement="custom",
            custom_required_total=Decimal("599"), custom_payment_terms="250 now, 349 later",
            payment_due_date=timezone.localdate() + timedelta(days=7),
        )
        calculate_realestate_deposit_amounts(self.enquiry)
        self.invoice = self.instalment()

    def instalment(self, amount="250"):
        return create_custom_instalment_invoice(enquiry=self.enquiry, amount=amount, description="Property visit")

    def run_stripe(self, remote, *, send=True):
        with patch("realestate.stripe_invoices.stripe", remote.api):
            return create_stripe_invoice(self.invoice, send=send)

    def assert_pending(self, stage):
        self.invoice.refresh_from_db()
        operation = self.invoice.stripe_sync_data["operations"][stage]
        self.assertTrue(operation["key"])
        self.assertFalse(operation["done"])
        self.assertIsNone(self.invoice.stripe_sync_lock_token)
        return deepcopy(operation)

    def age(self, stage):
        self.invoice.refresh_from_db()
        data = self.invoice.stripe_sync_data
        data["operations"][stage]["started"] = (timezone.now() - timedelta(hours=25)).isoformat()
        self.invoice.save(update_fields=("stripe_sync_data",))

    def test_never_attempted_can_be_locally_voided(self):
        void_local_realestate_invoice(self.invoice)
        self.assertEqual(self.instalment().total, Decimal("250"))
        self.assertEqual(self.enquiry.adjusted_required_total, Decimal("599"))

    def test_crash_before_first_remote_request_has_committed_marker(self):
        remote = StatefulStripe(fail="customer")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.assert_pending("customer")
        self.assertTrue(self.invoice.stripe_sync_data["creation_key"])
        self.assertEqual(remote.effects["customer"], 0)
        self.run_stripe(remote)
        self.assertEqual(remote.effects["create"], 1)

    def test_create_marker_is_visible_from_independent_connection_before_post(self):
        remote = StatefulStripe()
        def verify(**params):
            def read():
                close_old_connections()
                try:
                    i = RealEstateInvoice.objects.get(pk=self.invoice.pk)
                    return i.stripe_sync_state, i.stripe_sync_data["operations"]["create"]["key"]
                finally: connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as pool:
                state, key = pool.submit(read).result(timeout=10)
            self.assertEqual(state, "create_pending")
            self.assertEqual(key, params["idempotency_key"])
            return remote.call("create", params)
        remote.api.Invoice.create.side_effect = verify
        self.run_stripe(remote)

    def test_create_failure_before_execution_is_retryable(self):
        remote = StatefulStripe(fail="create")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.assert_pending("create")
        self.assertEqual(remote.effects["create"], 0)
        self.run_stripe(remote)
        self.assertEqual(remote.effects["create"], 1)

    def test_lost_create_response_recovers_same_invoice_next_calendar_day(self):
        remote = StatefulStripe(fail="create", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        original = self.assert_pending("create")
        self.assertEqual(self.invoice.stripe_invoice_id, "")
        with self.assertRaises(ValidationError): void_local_realestate_invoice(self.invoice)
        with self.assertRaises(ValidationError): self.instalment()
        with patch("realestate.stripe_invoices.timezone.localdate", return_value=timezone.localdate() + timedelta(days=1)):
            self.run_stripe(remote)
        calls = [params for name, params, ident in remote.calls if name == "create"]
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1]["idempotency_key"], original["key"])
        self.assertEqual(remote.effects["create"], 1)

    def test_known_identity_survives_finalization_failure(self):
        remote = StatefulStripe(fail="finalize")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.assert_pending("finalize")
        self.assertTrue(self.invoice.stripe_invoice_id)
        with self.assertRaises(ValidationError): void_local_realestate_invoice(self.invoice)
        self.run_stripe(remote)
        self.assertEqual(remote.api.Invoice.create.call_count, 1)
        self.assertEqual(remote.effects["finalize"], 1)

    def test_lost_finalization_response_replays_same_finalization(self):
        remote = StatefulStripe(fail="finalize", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.run_stripe(remote)
        self.assertEqual(remote.effects["create"], 1)
        self.assertEqual(remote.effects["finalize"], 1)

    def test_send_failure_preserves_finalization_and_retries_only_send(self):
        remote = StatefulStripe(fail="send")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        original = self.assert_pending("send")
        self.assertIsNotNone(self.invoice.stripe_invoice_finalized_at)
        self.run_stripe(remote)
        self.assertEqual(remote.api.Invoice.create.call_count, 1)
        self.assertEqual(remote.api.Invoice.finalize_invoice.call_count, 1)
        self.assertEqual(remote.api.Invoice.send_invoice.call_args.kwargs["idempotency_key"], original["key"])

    def test_lost_send_response_retries_without_duplicate_email(self):
        remote = StatefulStripe(fail="send", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.run_stripe(remote)
        self.run_stripe(remote)
        self.assertEqual(remote.effects["send"], 1)
        self.assertEqual(remote.api.Invoice.send_invoice.call_count, 2)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "sent")

    def test_create_without_send_then_create_send_honours_send(self):
        remote = StatefulStripe()
        self.run_stripe(remote, send=False)
        self.run_stripe(remote, send=True)
        self.assertEqual(remote.effects["create"], 1)
        self.assertEqual(remote.effects["send"], 1)

    def test_old_unknown_create_blocks_posts_void_and_replacement(self):
        remote = StatefulStripe(fail="create", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.age("create")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "recovery_required")
        self.assertEqual(remote.api.Invoice.create.call_count, 1)
        with self.assertRaises(ValidationError): void_local_realestate_invoice(self.invoice)
        with self.assertRaises(ValidationError): self.instalment()

    def test_old_unknown_send_requires_review_without_resending(self):
        remote = StatefulStripe(fail="send", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.age("send")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.assertEqual(remote.api.Invoice.send_invoice.call_count, 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "recovery_required")

    def test_line_item_response_loss_is_also_idempotent(self):
        remote = StatefulStripe(fail="item", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.run_stripe(remote)
        self.assertEqual(remote.effects["item"], 1)
        self.assertEqual(next(iter(remote.invoices.values()))["total"], 25000)

    def test_lost_local_identity_checkpoint_does_not_forget_attempt(self):
        from . import stripe_sync
        remote = StatefulStripe()
        original = stripe_sync.save_progress
        def fail(invoice, data, state, **fields):
            if "stripe_invoice_id" in fields:
                raise RuntimeError("Local checkpoint failed")
            return original(invoice, data, state, **fields)
        with patch("realestate.stripe_sync.save_progress", side_effect=fail), self.assertRaises(RuntimeError):
            self.run_stripe(remote)
        self.assert_pending("create")
        self.run_stripe(remote)
        self.assertEqual(remote.effects["create"], 1)

    def webhook(self, remote, event, status):
        payload = deepcopy(next(reversed(remote.invoices.values())))
        payload["status"] = status
        payload["amount_due"] = 0 if status == "void" else payload["total"]
        if status == "paid":
            payload["amount_paid"] = payload["total"]
            payload["payment_intent"] = "pi_" + payload["id"]
        with transaction.atomic(), patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve", return_value=payload):
            self.assertTrue(StripeWebhookView()._handle_realestate_invoice_event(event, payload))

    def test_validated_void_webhook_resolves_pending_send_and_permits_replacement(self):
        remote = StatefulStripe(fail="send", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.age("send")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.webhook(remote, "invoice.voided", "void")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "void_confirmed")
        self.assertEqual(self.instalment().total, Decimal("250"))

    def test_sent_webhook_resolves_unknown_send_without_another_post(self):
        remote = StatefulStripe(fail="send", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.webhook(remote, "invoice.sent", "open")
        self.run_stripe(remote)
        self.assertEqual(remote.api.Invoice.send_invoice.call_count, 1)

    def test_custom_599_250_349_end_to_end_with_paid_webhooks(self):
        remote = StatefulStripe()
        self.run_stripe(remote)
        self.webhook(remote, "invoice.paid", "paid")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "sent")
        self.assertEqual(self.enquiry.adjusted_balance_due, Decimal("349"))
        self.assertFalse(can_release_realestate_delivery(self.enquiry))
        self.invoice = self.instalment("349")
        self.run_stripe(remote)
        self.webhook(remote, "invoice.paid", "paid")
        self.assertEqual(self.enquiry.adjusted_balance_due, Decimal("0"))
        self.assertTrue(can_release_realestate_delivery(self.enquiry))
        amounts = [params["amount"] for name, params, ident in remote.calls if name == "item"]
        self.assertEqual(amounts, [25000, 34900])

    def test_enclosing_transaction_rejected_before_any_remote_post(self):
        remote = StatefulStripe()
        with transaction.atomic(), self.assertRaises(RuntimeError):
            self.run_stripe(remote)
        self.assertEqual(remote.calls, [])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "never")

    def test_concurrent_worker_cannot_advance_same_invoice(self):
        entered, release = threading.Event(), threading.Event()
        remote = StatefulStripe()
        def delayed(**params):
            entered.set()
            if not release.wait(timeout=10): raise RuntimeError("test timeout")
            return remote.call("create", params)
        remote.api.Invoice.create.side_effect = delayed
        def worker():
            close_old_connections()
            try: return self.run_stripe(remote)
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(timeout=10))
                with self.assertRaises(ValidationError): self.run_stripe(remote)
            finally: release.set()
            future.result(timeout=15)
        self.assertEqual(remote.effects["create"], 1)

    def test_admin_does_not_expose_frozen_payloads_or_claim(self):
        user = get_user_model().objects.create_superuser("recovery_admin", "admin@example.invalid", "password")
        request = RequestFactory().get("/"); request.user = user
        model_admin = RealEstateInvoiceAdmin(RealEstateInvoice, custom_admin_site)
        fields = model_admin.get_fields(request, self.invoice)
        self.assertIn("stripe_sync_state", fields)
        for private in ("stripe_sync_data", "stripe_sync_lock_token", "stripe_sync_lock_until"):
            self.assertNotIn(private, fields)

    def test_legacy_migration_does_not_assume_blank_id_means_no_attempt(self):
        import importlib
        from copy import copy
        from types import SimpleNamespace
        from django.apps import apps
        known = copy(self.invoice)
        known.pk = None
        known.invoice_number = "LEGACY-KNOWN"
        known.stripe_invoice_id = "in_legacy"
        known.save()
        draft = copy(self.invoice)
        draft.pk = None
        draft.invoice_number = "LEGACY-DRAFT"
        draft.status = "draft"
        draft.save()
        migration = importlib.import_module("realestate.migrations.0032_stripe_invoice_recovery")
        migration.flag_legacy_invoices(apps, SimpleNamespace(connection=connection))
        self.invoice.refresh_from_db(); known.refresh_from_db(); draft.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "recovery_required")
        self.assertEqual(known.stripe_sync_state, "legacy_remote")
        self.assertEqual(known.stripe_invoice_id, "in_legacy")
        self.assertEqual(draft.stripe_sync_state, "never")
        with self.assertRaises(ValidationError): void_local_realestate_invoice(self.invoice)

    def test_sdk_error_payload_is_not_shown_to_admin(self):
        remote = StatefulStripe()
        remote.api.Invoice.create.side_effect = RuntimeError("private_email@example.invalid sk_secret_example")
        with self.assertRaises(ValidationError) as caught:
            self.run_stripe(remote)
        self.assertNotIn("private_email", str(caught.exception))
        self.assertNotIn("sk_secret", str(caught.exception))

    def test_changed_remote_identity_does_not_replay_old_send(self):
        remote = StatefulStripe(fail="send", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.invoice.refresh_from_db()
        self.invoice.stripe_invoice_id = "in_validated_revision"
        self.invoice.save(update_fields=("stripe_invoice_id",))
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "recovery_required")
        self.assertEqual(remote.api.Invoice.send_invoice.call_count, 1)

    def test_expired_worker_claim_can_be_recovered(self):
        import uuid
        self.invoice.stripe_sync_lock_token = uuid.uuid4()
        self.invoice.stripe_sync_lock_until = timezone.now() - timedelta(minutes=1)
        self.invoice.save(update_fields=("stripe_sync_lock_token", "stripe_sync_lock_until"))
        remote = StatefulStripe()
        self.run_stripe(remote)
        self.invoice.refresh_from_db()
        self.assertIsNone(self.invoice.stripe_sync_lock_token)
        self.assertEqual(remote.effects["create"], 1)

    def test_sent_ancestor_does_not_imply_revision_was_sent(self):
        remote = StatefulStripe()
        self.run_stripe(remote)
        self.invoice.refresh_from_db()
        self.invoice.stripe_invoice_id = "in_later_revision"
        self.invoice.save(update_fields=("stripe_invoice_id",))
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "legacy_remote")
        self.assertEqual(remote.effects["send"], 1)

    def test_expired_line_item_attempt_cannot_add_another_line(self):
        remote = StatefulStripe(fail="item", after=True)
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.age("item")
        with self.assertRaises(ValidationError): self.run_stripe(remote)
        self.assertEqual(remote.effects["item"], 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_sync_state, "recovery_required")

    def test_reminder_retry_reuses_key_but_confirmed_reminder_can_be_sent_again(self):
        remote = StatefulStripe()
        self.run_stripe(remote)
        remote.fail, remote.failed, remote.after = "send", False, True
        with patch("realestate.stripe_invoices.stripe", remote.api), patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve", side_effect=remote.api.Invoice.retrieve):
            with self.assertRaises(ValidationError): send_stripe_invoice(self.invoice)
            send_stripe_invoice(self.invoice)
        self.assertEqual(remote.effects["send"], 2)  # Initial send + one deliberate reminder.
