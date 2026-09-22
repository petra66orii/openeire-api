"""Durable checkpoints around Stripe POSTs; remote effects are never rolled back."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import RealEstateInvoice


State = RealEstateInvoice.StripeSyncState
# Leave margin before Stripe's minimum 24-hour idempotency retention expires.
REPLAY_WINDOW = timedelta(hours=23)
CLAIM_LIFETIME = timedelta(minutes=10)
STATES = {"customer": State.CUSTOMER, "create": State.CREATE,
          "item": State.ITEM, "finalize": State.FINALIZE, "send": State.SEND}
RECOVERY_INSTRUCTIONS = (
    "Do not void, recreate, or clear Stripe references. Review Stripe using the local invoice "
    "number and metadata. Ask an engineer to reconcile the matching remote invoice and "
    "confirm whether it was sent/voided. Retry only after that review."
)


def value(obj, key, default=""):
    return obj.get(key, default) if hasattr(obj, "get") else getattr(obj, key, default)


@contextmanager
def claim(invoice):
    # durable=True rejects application callers inside an enclosing transaction.
    # Django's TestCase wrapper is exempt; durability tests use TransactionTestCase.
    token = uuid.uuid4()
    with transaction.atomic(durable=True):
        current = RealEstateInvoice.objects.select_for_update().get(pk=invoice.pk)
        if current.status not in {RealEstateInvoice.Status.ISSUED, RealEstateInvoice.Status.PARTIALLY_PAID}:
            raise ValidationError("Only issued unpaid invoices can be processed in Stripe.")
        if current.stripe_sync_lock_until and current.stripe_sync_lock_until > timezone.now():
            raise ValidationError("Stripe processing is already running. Retry after it finishes (or after ten minutes if it crashed).")
        current.stripe_sync_lock_token = token
        current.stripe_sync_lock_until = timezone.now() + CLAIM_LIFETIME
        if current.stripe_sync_state == State.NEVER and not current.stripe_invoice_id:
            current.stripe_sync_state = State.CREATE
        current.save(update_fields=("stripe_sync_lock_token", "stripe_sync_lock_until", "stripe_sync_state"))
    try:
        yield token
    finally:
        with transaction.atomic(durable=True):
            RealEstateInvoice.objects.filter(pk=invoice.pk, stripe_sync_lock_token=token).update(
                stripe_sync_lock_token=None, stripe_sync_lock_until=None,
            )


def locked(invoice, token):
    current = RealEstateInvoice.objects.select_for_update().get(pk=invoice.pk)
    if current.stripe_sync_lock_token != token:
        raise ValidationError("Stripe processing claim expired. Retry to read the saved progress.")
    return current


def save_progress(invoice, data, state, **fields):
    invoice.stripe_sync_data = data
    invoice.stripe_sync_state = state
    for name, val in fields.items():
        setattr(invoice, name, val)
    invoice.save(update_fields=("stripe_sync_data", "stripe_sync_state", "updated_at", *fields))


def initialize(invoice, token, payloads):
    with transaction.atomic(durable=True):
        current = locked(invoice, token)
        if current.stripe_sync_state == State.RECOVERY:
            raise ValidationError(RECOVERY_INSTRUCTIONS)
        if not current.stripe_sync_data:
            save_progress(current, {
                "creation_key": f"re-{current.pk}-create-{uuid.uuid4().hex}",
                "payloads": payloads, "operations": {},
            }, State.CREATE)
    return current


def post(invoice, token, name, api_call, *, payload=None, remote_id=None):
    """Commit the exact request before calling Stripe, then commit its result."""
    expired = False
    with transaction.atomic(durable=True):
        current = locked(invoice, token)
        if current.status in {RealEstateInvoice.Status.VOID, RealEstateInvoice.Status.PAID}:
            raise ValidationError("The invoice is paid or void; no further Stripe POST is allowed.")
        data = deepcopy(current.stripe_sync_data)
        operations = data.setdefault("operations", {})
        operation = operations.get(name)
        if operation and operation.get("done"):
            return operation["result"]
        if current.stripe_sync_state == State.RECOVERY:
            raise ValidationError(RECOVERY_INSTRUCTIONS)
        target_changed = operation and operation.get("remote_id") and operation["remote_id"] != remote_id
        if operation and (target_changed or timezone.now() - datetime.fromisoformat(operation["started"]) >= REPLAY_WINDOW):
            data["recovery_reason"] = (
                f"Remote invoice changed during an unconfirmed {name} attempt."
                if target_changed else f"Unconfirmed {name} attempt is older than the safe replay window."
            )
            save_progress(current, data, State.RECOVERY)
            expired = True
        else:
            if not operation:
                operation = {
                    "key": data["creation_key"] if name == "create" else f"re-{current.pk}-{name}-{uuid.uuid4().hex}",
                    "started": timezone.now().isoformat(),
                    "payload": deepcopy(payload or {}),
                    "remote_id": remote_id,
                    "done": False,
                }
                operations[name] = operation
            save_progress(current, data, STATES[name])
    if expired:
        raise ValidationError(RECOVERY_INSTRUCTIONS)

    params = deepcopy(operation["payload"])
    params["idempotency_key"] = operation["key"]
    try:
        result = (
            api_call(operation["remote_id"], **params)
            if operation["remote_id"] else api_call(**params)
        )
    except Exception as exc:
        # SDK errors can contain customer/request details; keep them out of admin messages.
        raise ValidationError(
            f"Stripe {name} was not confirmed. Progress is saved; retry within the safe replay window. "
            "For older attempts, review Stripe before proceeding."
        ) from exc
    result_id = str(value(result, "id", "") or "")
    if name in {"customer", "create"} and not result_id.startswith("cus_" if name == "customer" else "in_"):
        raise ValidationError("Stripe returned no valid identity. Saved attempt remains pending; retry safely.")
    snapshot = {key: value(result, key) for key in (
        "id", "number", "status", "hosted_invoice_url", "invoice_pdf", "created",
    )}
    snapshot = {key: val if isinstance(val, (str, int, float, bool)) else "" for key, val in snapshot.items()}
    with transaction.atomic(durable=True):
        current = locked(invoice, token)
        data = deepcopy(current.stripe_sync_data)
        data["operations"][name].update(done=True, result=snapshot)
        fields = {}
        state = STATES[name]
        if name == "create":
            # Never overwrite a newer identity learned from a validated webhook.
            if not current.stripe_invoice_id:
                fields["stripe_invoice_id"] = result_id
                fields["stripe_invoice_created_at"] = timezone.now()
            state = State.ITEM
        if name == "finalize":
            fields["stripe_invoice_finalized_at"] = timezone.now()
            state = State.FINALIZED
        if name == "send":
            state = State.SENT
            data["confirmed_sent_id"] = operation["remote_id"]
        if name in {"create", "finalize", "send"}:
            for local, remote in (
                ("stripe_invoice_number", "number"), ("stripe_invoice_status", "status"),
                ("stripe_hosted_invoice_url", "hosted_invoice_url"), ("stripe_invoice_pdf_url", "invoice_pdf"),
            ):
                if snapshot[remote]:
                    fields[local] = str(snapshot[remote])
        if current.stripe_sync_state == State.SENT:
            state = State.SENT
        if current.status == RealEstateInvoice.Status.VOID:
            state = State.VOID
        elif name in {"finalize", "send"} and current.stripe_invoice_id != operation["remote_id"]:
            data["recovery_reason"] = "Remote identity changed while a Stripe operation was in flight."
            fields = {}
            state = State.RECOVERY
        save_progress(current, data, state, **fields)
    return snapshot


def resume(invoice, api, payload_factory, *, send=False, reminder=False):
    with claim(invoice) as token:
        current = RealEstateInvoice.objects.select_related("enquiry").get(pk=invoice.pk)
        had_remote = bool(current.stripe_invoice_id)
        if current.status not in {RealEstateInvoice.Status.ISSUED, RealEstateInvoice.Status.PARTIALLY_PAID}:
            raise ValidationError("Only issued unpaid invoices can be created in Stripe.")
        if current.stripe_sync_state == State.SENT and not reminder:
            if current.stripe_sync_data.get("confirmed_sent_id") == current.stripe_invoice_id:
                return current, False
            # A send of an ancestor does not establish that its revision was sent.
            with transaction.atomic(durable=True):
                fresh = locked(invoice, token)
                save_progress(fresh, fresh.stripe_sync_data, State.LEGACY)
            current.refresh_from_db()
        if current.stripe_sync_state == State.RECOVERY:
            raise ValidationError(RECOVERY_INSTRUCTIONS)
        if had_remote and (not current.stripe_sync_data or current.stripe_sync_state == State.LEGACY) and not reminder:
            if not send:
                return current, False
            raise ValidationError("Historic Stripe sending history is unknown. Review Stripe first; use the explicit reminder action only if sending is intended.")

        if reminder:
            # Read/validate revisions before selecting the send target. This makes
            # no POST and preserves the existing revision identity protections.
            from .stripe_invoice_revisions import reconcile_stored_invoice_revision
            current, _ = reconcile_stored_invoice_revision(current)
            if current.stripe_invoice_status in {"void", "paid", "uncollectible"}:
                raise ValidationError("The current Stripe invoice cannot be sent in its present status.")
            with transaction.atomic(durable=True):
                fresh = locked(invoice, token)
                data = deepcopy(fresh.stripe_sync_data) or {"operations": {}}
                operation = data.setdefault("operations", {}).get("send")
                if operation and not operation.get("done") and operation.get("remote_id") != fresh.stripe_invoice_id:
                    data["recovery_reason"] = "Invoice revised while an earlier send outcome remains unknown."
                    save_progress(fresh, data, State.RECOVERY)
                elif not operation or operation.get("done"):
                    data["operations"].pop("send", None)
                    data["send_is_reminder"] = True
                    save_progress(fresh, data, State.FINALIZED)
            current.refresh_from_db()
            send = True

        if not had_remote or "payloads" in current.stripe_sync_data:
            if not current.stripe_sync_data:
                current = initialize(current, token, payload_factory(current))
            frozen = current.stripe_sync_data["payloads"]
            customer_id = frozen.get("customer_id")
            if not current.stripe_invoice_id:
                if not customer_id:
                    customer = post(current, token, "customer", api.Customer.create, payload=frozen["customer"])
                    customer_id = customer["id"]
                    # The operation result is already durable, independently of this compatibility field.
                    current.enquiry.stripe_customer_id = customer_id
                    current.enquiry.save(update_fields=("stripe_customer_id", "updated_at"))
                payload = {**frozen["create"], "customer": customer_id}
                post(current, token, "create", api.Invoice.create, payload=payload)
                current.refresh_from_db()
            if not current.stripe_invoice_finalized_at and current.stripe_invoice_status not in {"open", "paid", "void", "uncollectible"}:
                if not customer_id:
                    customer_id = current.stripe_sync_data["operations"]["customer"]["result"]["id"]
                post(current, token, "item", api.InvoiceItem.create,
                     payload={**frozen["item"], "customer": customer_id, "invoice": current.stripe_invoice_id})
                post(current, token, "finalize", api.Invoice.finalize_invoice, remote_id=current.stripe_invoice_id)
                current.refresh_from_db()
        if send:
            post(current, token, "send", api.Invoice.send_invoice, remote_id=current.stripe_invoice_id)
        current.refresh_from_db()
        return current, not had_remote


def record_validated_event(invoice, event_type):
    """Called only after invoice identity/revision/amount validation, in the webhook transaction."""
    if event_type not in {"invoice.sent", "invoice.paid", "invoice.voided"}:
        return
    data = deepcopy(invoice.stripe_sync_data)
    if event_type == "invoice.voided" and invoice.status == RealEstateInvoice.Status.VOID:
        state = State.VOID
    elif event_type == "invoice.paid":
        state = State.SENT if invoice.stripe_sync_state == State.SENT else State.FINALIZED
    elif event_type == "invoice.sent":
        if data.get("send_is_reminder"):
            # This event could be a delayed notification of an earlier send.
            # Only the idempotent POST response confirms this particular reminder.
            return
        state = State.SENT
        data["confirmed_sent_id"] = invoice.stripe_invoice_id
        operation = data.get("operations", {}).get("send")
        if operation and operation.get("remote_id") == invoice.stripe_invoice_id:
            operation.update(done=True, result={"id": invoice.stripe_invoice_id, "status": invoice.stripe_invoice_status})
    else:
        return
    data.pop("recovery_reason", None)
    save_progress(invoice, data, state)
