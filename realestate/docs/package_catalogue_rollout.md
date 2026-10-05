# Residential catalogue rollout: October 2026

## Effective scope

New schema-v3 public enquiries use catalogue `residential_2026_10`:

- Starter: EUR 259; typically 25–30 professionally edited interior and exterior photographs, 5–8 edited drone stills and a measured 2D floor plan.
- Pro: EUR 419; typically 30–35 photographs, the Starter supporting deliverables, one combined cinematic 4K property film using ground and aerial footage, approximately 2–3 minutes where the property and agreed brief justify it, and one separate vertical 9:16 social-media edit.
- Premium: EUR 549; typically 35–40 photographs, the Pro deliverables and one hosted 3D virtual tour for a suitable standard-sized property.
- Custom / POA: scope and price are reviewed before booking.

Photo ranges are indicative. An explicitly agreed photograph count overrides the range and is persisted with the enquiry and agreement snapshot.

Essential remains in both catalogues for historical records and discretionary internal mini-shoots. It is not accepted by the public API at any schema version and is not a public package choice.

The current add-on names and guidance are defined in the model and invoice services. Included deliverables are rejected as duplicate add-ons by the serializer and omitted from invoice itemisation. Extended Property Film is scope-priced; its EUR 150 amount is internal starting guidance rather than a public fixed-price promise. The historical `extended_drone_video` key and wording remain available only to legacy records.

## Version and transition rule

- Schema-v1 and schema-v2 submissions are assigned `residential_2026_09` for rolling-deployment compatibility.
- Schema-v3 submissions are assigned `residential_2026_10`.
- Existing rows are backfilled to `residential_2026_09` by migration `0035`; no historical snapshot content is rewritten.
- Already issued quotations remain valid under the catalogue and scope with which they were issued.
- Unfinished or unissued selections must be reviewed before being converted to the new catalogue.

## Historical protection

Issued quotation content is stored in immutable `RealEstateQuotationSnapshot` rows, including the catalogue identifier, recipient, subject, render context and exact rendered text/HTML bodies.

Issued Booking Agreement snapshots remain the contractual source for existing bookings. Agreement rendering and invoice construction resolve the enquiry's recorded catalogue version. Existing agreement, invoice line-item and payment snapshots are not mutated.

For booked or completed records without a recoverable agreement snapshot, package-scope rendering continues to fail closed so an operator must verify the issued scope before new client communication is generated.

Known historical fixed scopes remain unchanged, including the existing 20- and 25-photograph Starter/Pro records. Legacy add-on labels remain resolvable for their historical documents.

New Booking Agreement snapshots use template version 2.1. Earlier stored Markdown remains immutable and renders from its snapshot rather than the current catalogue.

## Deployment order

1. Deploy the backend release candidate.
2. Apply migration `0035_residential_catalogue_2026_10`.
3. Verify schema-v3 enquiry creation, quotation snapshot creation, agreement generation and invoice preview in a production-safe QA environment.
4. Deploy the frontend using Node 22.x build output.
5. Verify Starter, Pro, Premium and Custom journeys; package metadata/JSON-LD; and one test submission.
6. Activate the coordinated public cutover only after manual approval.

Backend-first deployment is safe because schema-v2 remains supported during the rolling window. The frontend must not be deployed first because schema-v3 is intentionally rejected by an older backend.

## Rollback

Roll back the frontend first to stop new schema-v3 submissions, then roll back backend application code if required. Do not reverse migration `0035` after new quotation snapshots or catalogue-v3 enquiries exist; leave its additive columns and tables in place. Existing records retain their per-row catalogue version and immutable snapshots.

## Manually maintained materials

The seven named Notion documents and all print artwork are outside this release candidate and must be synchronised separately after approval. Also audit saved CRM snippets, proposal templates, price sheets, social posts, marketplace listings and staff operating notes. Never edit issued or signed historical material.
