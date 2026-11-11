# Enquiry email recovery

Each new enquiry tracks delivery of the internal notification and customer confirmation separately. Failed sends leave the corresponding timestamp empty and record the most recent error. The public enquiry still returns 201 once the enquiry is saved.

Run `python manage.py retry_realestate_enquiry_emails --limit 100 --fail-on-error` from the production application environment to retry pending messages. Schedule this command every five minutes in Render and alert on a non-zero exit. Pending enquiries with no previous attempt run first; subsequently, those least recently attempted run first, so persistent failures cannot block newer enquiries.

Each email kind is claimed with a short atomic database update before contacting the mail provider. The network call runs without a database transaction or row lock. Fresh claims are skipped by concurrent workers, failed claims are released for retry, and claims older than `ENQUIRY_EMAIL_CLAIM_TIMEOUT_SECONDS` (600 seconds by default) can be recovered after a crashed worker. Sent messages are skipped on later runs.

The migration marks pre-existing enquiries as handled at their creation time because their historical delivery state cannot be reconstructed. Review old mail failures manually; the command does not resend historical client messages.

Email delivery is not an atomic database operation. If the SMTP provider accepts a message but the process dies before recording its timestamp, a subsequent retry may send a duplicate. Moving these messages to Brevo's transactional API with a stable idempotency key is needed to eliminate that final provider-acceptance window.
