# Real-estate enquiry form schema rollout

The public enquiry endpoint temporarily supports two contracts:

- An omitted `form_schema_version` or explicit version `1` is treated as the
  deployed legacy form. It retains the original mandatory fields, add-ons and
  consent validation.
- `form_schema_version: 2` activates the complete structured shoot-scoping
  contract. Every V2 conditional, controlled choice, location, date, access,
  readiness, presenter and package/add-on rule remains mandatory.

The submitted version is stored on `RealEstateEnquiry.form_schema_version`.
Blank values identify legacy clients that omitted the version.

## Deployment order

1. Apply the additive migrations `0020` and `0021` in the release/migration
   phase before starting new backend workers. The old backend ignores the new
   nullable/defaulted columns.
2. Deploy the compatible backend serializer.
3. Confirm the legacy production form can still create an enquiry.
4. Deploy the frontend, which always sends `form_schema_version: 2`.
5. Monitor validation-error rates and the persisted schema-version field.

The backend must remain backward compatible if the frontend deployment is
rolled back.

## Removing legacy acceptance

Legacy acceptance can be removed only after:

1. Every production frontend instance sends version 2.
2. The maximum CDN/browser cache lifetime has elapsed after that deployment.
3. No new enquiries with a blank or version-1 schema have been recorded for an
   agreed safety window (recommended: at least seven consecutive days).
4. Any external API clients or integrations have been confirmed as upgraded.

Use an operational query grouped by `form_schema_version` and `created_at` to
verify the cutoff; do not infer readiness only from the frontend deployment
time.

In the removal release:

1. Make `form_schema_version` required by the public serializer and accept only
   version 2.
2. Delete the legacy validation branch and its success tests.
3. Replace them with tests asserting omitted/version-1 payloads receive a clear
   `400` response.
4. Keep the nullable database field and historical values for auditability;
   no data backfill or destructive migration is required.
