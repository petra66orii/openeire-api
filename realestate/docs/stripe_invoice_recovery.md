# Stripe invoice recovery

Local instalment amounts and booking totals are unchanged. Stripe processing is
a sequence of durable checkpoints, not a transaction wrapping remote requests.
Call `create_stripe_invoice` / `send_stripe_invoice` outside application atomic
blocks (including `ATOMIC_REQUESTS`); the service rejects an enclosing transaction.

## Progress and retries

The invoice stores `stripe_sync_state`, private `stripe_sync_data`, and a
ten-minute processing claim. Each customer/create/item/finalise/send POST has
its own persisted key, exact parameters, first-attempt time and completion result.
Frozen data contains customer details, not API keys, and is excluded from admin.
All markers commit before POST; invoice identity commits before item creation,
finalisation or sending. An expired claim can be reclaimed after a crashed worker.

Ordinary Create and send retries resume pending operations with the same key and
request, including the original `days_until_due`. A known invoice ID is never
created again. Confirmed sends are no-ops. The explicit reminder action starts a
new send only after the previous send is confirmed; pending reminders reuse their
saved key. Revision validation is retained when selecting a reminder target.

Unconfirmed operations are replayed only for 23 hours from their first attempt,
leaving margin before the minimum 24-hour idempotency retention. Beyond this,
or if a revision changes the target of a pending send, automatic POSTs stop and
the invoice enters Recovery required. There is no automatic reset or blind
metadata-search-and-create fallback.

## Recovery required

1. Do not clear state/IDs, locally void the invoice, or issue replacement debt.
2. Review Stripe's invoice list, request logs and sent-email history using the
   local invoice number and `realestate_invoice_number` metadata. Check the mode,
   enquiry, currency and exact invoice amount. An absent search result alone is
   not proof that a timed-out request never executed.
3. Ask an engineer to reconcile the matching remote identity and operation
   outcome using the existing invoice/revision validation rules. Unknown-ID
   attempts and ambiguous old sends require this manual review; the admin does
   not offer an unsafe reset button.
4. A validated `invoice.sent`/`invoice.paid`/`invoice.voided` webhook updates the
   corresponding local outcome. A confirmed remote void permits replacement
   instalments; never delete remote references to force local-only void.

Local-only void requires never-attempted state, no workflow data/claim, and all
existing no-payment/no-Stripe-reference checks. Supersession has the same guard.

## Deployment

1. Back up the database; pause invoice actions and stop old application workers
   before migrating, so old code cannot create untracked remote side effects.
2. Deploy code and run `python manage.py migrate --noinput` (includes 0032).
3. Run `python manage.py check`; restart web/background workers with the new code.
4. Review invoices filtered by Recovery required before resuming invoice actions.
   Migration 0032 conservatively flags existing non-draft invoices without a
   Stripe invoice ID, including paid/void local records: old failed POST history
   cannot be inferred from a blank ID. Existing known IDs are retained and marked
   as historical sending history unknown. No Stripe calls occur in migration.
5. Do not roll back to the old Stripe workflow or drop these fields after use.

For a new Shangarry booking with no invoices: save Custom total 599 and the
250/349 terms, create a 250 instalment and use Create/send. Once payment is
reconciled, check balance 349 and create/send that instalment. Never change the
whole-booking total to an instalment amount. Review any existing migration
warning first. No real send or production migration was performed during tests.
