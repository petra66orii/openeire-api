# Enquiry email recovery

Each new enquiry tracks delivery of the internal notification and customer confirmation separately. Failed sends leave the corresponding timestamp empty and record the most recent error. The public enquiry still returns 201 once the enquiry is saved.

Run `python manage.py retry_realestate_enquiry_emails --limit 100` from the production application environment to retry pending messages. Schedule this command periodically in the deployment scheduler and monitor its output. It is safe to run concurrently: it locks an enquiry while checking and sending. Sent messages are skipped on later runs.

The migration marks pre-existing enquiries as handled at their creation time because their historical delivery state cannot be reconstructed. Review old mail failures manually; the command does not resend historical client messages.

Email delivery is not an atomic database operation. If a mail provider accepts a message but the process dies before recording its timestamp, a subsequent retry may send a duplicate. A provider with idempotent message submission is needed to eliminate that window.
