# Real-estate package catalogue rollout

## Effective scope

The package rules in `realestate.package_catalogue` apply to enquiries quoted
or contracted after the backend catalogue release is deployed:

- Essential: EUR 175; 10 ground photographs
- Starter: EUR 259; 25 ground photographs, 5-8 additional aerial stills and a measured 2D floor plan
- Pro: EUR 419; 30 ground photographs, 5-8 additional aerial stills, a measured 2D floor plan, One combined 4K property film — 60–90 sec ground footage + 60–90 sec aerial footage (approx. 2–3 min total), and one separate vertical 9:16 social video
- Premium: EUR 549; 35 ground photographs, 5-8 additional aerial stills, a measured 2D floor plan, One combined 4K property film — 60–90 sec ground footage + 60–90 sec aerial footage (approx. 2–3 min total), one separate vertical 9:16 social video and a hosted 3D tour
- Custom / Not sure: specifically agreed

Additional edited photographs remain EUR 10 per photograph. The EUR 75 floor-plan
add-on is available for Essential and suitable Custom work, and is rejected as a
duplicate selection for Starter, Pro and Premium. Alternative social formats,
additional cuts and extra edits remain available for EUR 50.

## Historical protection

An issued Booking Agreement snapshot is the contractual source for an existing
booking. Customer and operations workflows resolve package scope from the
latest snapshot context and never rebuild that scope from the current
catalogue.

For booked or completed records without a recoverable agreement snapshot,
package-scope rendering fails closed with an instruction to verify the issued
quotation or agreement. An operator must confirm and persist the agreed scope
before using it in a new client communication or regenerated document.

Known historical scopes that must not be changed:

- Kevin's completed Pro booking: 25 photographs
- Kathleen's completed Pro booking: 25 photographs
- Brid's Willow Lodge Starter booking: 20 photographs
- Brid's 6 Waters Edge Starter booking: 20 photographs

No migration backfills or rewrites existing snapshots, timelines, invoices, or
communications.

## Agreement version

New Booking Agreement snapshots use template version 1.8. Versions 1.6 and 1.7
remain immutable stored Markdown and continue to render byte-for-byte from
their snapshots.

Migration `0023` changes only the model-field default for new snapshot rows.
It contains no data operation.

## Deployment order

1. Deploy the backend and apply schema migration `0023`.
2. Verify the public enquiry API returns the new catalogue values and that a
   test version-1.8 agreement renders correctly.
3. Deploy the frontend.
4. Verify package cards, enquiry guidance, metadata/JSON-LD, and a test
   submission in production-safe QA.

The backend-first order makes the API authoritative before the public site
advertises the revised allowances.

## Manually maintained materials

Before launch, check any material outside these repositories, including sales
PDFs, saved email snippets, CRM templates, proposal templates, price sheets,
social posts, marketplace listings, and staff operating notes. Issued or
signed historical material must not be edited.
