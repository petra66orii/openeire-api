# Enquiry email recovery

Each new enquiry tracks delivery of the internal notification and customer confirmation separately. Failed sends leave the corresponding timestamp empty and record the most recent error. The public enquiry still returns 201 once the enquiry is saved.

Run `python manage.py retry_realestate_enquiry_emails --limit 100 --fail-on-error` from the production application environment to retry pending messages. Schedule this command every five minutes in Render and alert on a non-zero exit. The five-minute interval keeps recovery inside Brevo's 15-minute idempotency window. Pending enquiries with no previous attempt run first; subsequently, those least recently attempted run first, so persistent failures cannot block newer enquiries.

Each email kind is claimed with a short atomic database update before contacting the mail provider. The network call runs without a database transaction or row lock. Fresh claims are skipped by concurrent workers, failed claims are released for retry, and claims older than `ENQUIRY_EMAIL_CLAIM_TIMEOUT_SECONDS` (300 seconds by default) can be recovered after a crashed worker. Sent messages are skipped on later runs.

The migration marks pre-existing enquiries as handled at their creation time because their historical delivery state cannot be reconstructed. Review old mail failures manually; the command does not resend historical client messages.

Production enquiry messages use Brevo's transactional API with a stable UUID
idempotency key per enquiry and recipient kind. Retries inside Brevo's 15-minute
window are provider-deduplicated even if a worker dies after acceptance but
before recording the sent timestamp. Development and explicitly disabled API
configurations retain the existing SMTP behaviour.
