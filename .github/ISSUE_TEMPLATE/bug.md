---
name: Bug
about: Report a bug in the system
title: "BUG:"
labels: bug
assignees: petra66orii
---

## Summary

A short description of the bug.

---

## Severity

- [ ] Critical (system broken / data loss)
- [ ] High (core feature unusable)
- [ ] Medium (feature partially broken)
- [ ] Low (minor issue / cosmetic)

---

## Environment

- App Version / Branch:
- Browser / Device (if frontend):
- OS:
- Python Version:
- Database:
- Deployment Environment:
  - [ ] Local
  - [ ] Staging
  - [ ] Production

---

## Steps to Reproduce

1.
2.
3.

---

## Expected Behaviour

Describe what should have happened.

---

## Actual Behaviour

Describe what actually happened.

---

## Screenshots / Evidence

Attach screenshots, screen recordings, or logs if available.

---

## Logs / Error Messages

Paste stack traces or relevant logs.

---

## Affected Components

Examples:

- API
- Django Model
- Stripe Webhook
- React Component
- Admin Panel
- AI Worker
- Licensing System

---

## Root Cause (if known)

Explain what caused the issue.

Example:
Stripe webhook processed twice due to missing idempotency check.

---

## Solution

Explain how the issue was fixed or how it should be fixed.

---

## Regression Risk

Does this change risk affecting other systems?

Example:

- Payment processing
- Licence generation
- Asset downloads

---

## Commit / Pull Request

PR:

Commit:

---

## Post-Mortem Notes (Optional)

Lessons learned from this bug.
