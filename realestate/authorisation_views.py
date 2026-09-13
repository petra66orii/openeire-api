from datetime import timedelta
import logging

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from django.db import transaction

from .authorisation_forms import AuthorisationForm, IssueAuthorisationForm, ScopeCheckForm
from .authorisations import accept_authorisation, document_sections, is_available, issue_authorisation, send_authorisation
from .models import PropertyAuthorisation

logger = logging.getLogger(__name__)


def private_response(response):
    response["Cache-Control"] = "no-store, private"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


def pdf_response(document):
    response = HttpResponse(bytes(document.accepted_pdf if document.accepted_at else document.issued_pdf), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="property-access-authorisation.pdf"'
    return private_response(response)


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def public_authorisation(request, token, pdf=False):
    document = PropertyAuthorisation.objects.filter(token=token).first()
    if not document or not is_available(document):
        return private_response(render(request, "realestate/authorisation.html", {"unavailable": True}, status=410))
    if pdf:
        if request.method != "GET":
            return private_response(HttpResponse(status=405))
        return pdf_response(document)
    if request.method == "GET" and request.GET.get("download") == "1":
        return private_response(render(request, "realestate/authorisation.html", {"document": document, "download_only": True}))
    form = None
    status = 200
    if not document.accepted_at:
        form = AuthorisationForm(request.POST if request.method == "POST" else None, initial=document.issued_snapshot["initial"], video_included=document.issued_snapshot["video_included"])
        if request.method == "POST" and form.is_valid():
            try:
                accept_authorisation(document, request.POST)
            except ValueError as exc:
                form.add_error(None, str(exc))
                status = 409
            else:
                return private_response(redirect("property-authorisation", token=token))
        elif request.method == "POST":
            status = 400
    elif request.method == "POST":
        status = 409
    snapshot = document.accepted_snapshot or document.issued_snapshot
    return private_response(render(request, "realestate/authorisation.html", {"document": document, "snapshot": snapshot, "sections": document_sections(snapshot), "form": form}, status=status))


def hub_context(enquiry):
    documents = list(enquiry.property_authorisations.all())
    current = [doc for doc in documents if not doc.superseded_at and not doc.revoked_at]
    awaiting = not current or any(not doc.accepted_at for doc in current)
    shoot = enquiry.shoot_date or enquiry.proposed_shoot_date or enquiry.preferred_date
    warning = awaiting and shoot and timezone.localdate() <= shoot <= timezone.localdate() + timedelta(days=7)
    return {"documents": documents, "awaiting": awaiting, "warning": bool(warning), "checks": enquiry.property_scope_checks.select_related("recorded_by").all()[:10]}


@require_http_methods(["GET", "POST"])
@csrf_protect
def staff_authorisation(request, admin, enquiry, action):
    if not admin.has_change_permission(request, enquiry):
        raise PermissionDenied
    if action not in {"authorisation-issue", "authorisation-reissue", "authorisation-review", "authorisation-pdf", "authorisation-send", "authorisation-handwritten", "authorisation-revoke", "authorisation-scope-check"}:
        return HttpResponse("Unknown authorisation action.", status=404)
    document = None
    doc_id = request.POST.get("document") or request.GET.get("document")
    if doc_id:
        document = get_object_or_404(PropertyAuthorisation, pk=doc_id, enquiry=enquiry)
    if action in ("authorisation-review", "authorisation-pdf", "authorisation-send", "authorisation-handwritten", "authorisation-reissue", "authorisation-revoke") and not document:
        return HttpResponse("Select an authorisation.", status=400)
    if action == "authorisation-pdf":
        return pdf_response(document)
    form = None
    snapshot = None
    error = ""
    if action in ("authorisation-issue", "authorisation-reissue"):
        form = IssueAuthorisationForm(request.POST if request.method == "POST" else None, initial={"location": document.location if document else enquiry.property_address, "recipient_email": document.recipient_email if document else enquiry.email})
    elif action == "authorisation-handwritten":
        form = AuthorisationForm(request.POST if request.method == "POST" else None, initial=document.issued_snapshot["initial"], video_included=document.issued_snapshot["video_included"], handwritten=True)
    elif action == "authorisation-scope-check":
        form = ScopeCheckForm(request.POST if request.method == "POST" else None, initial={"location": enquiry.property_address})
    if request.method == "POST" and (not form or form.is_valid()):
        try:
            if action in ("authorisation-issue", "authorisation-reissue"):
                new = issue_authorisation(enquiry, user=request.user, supersedes=document if action.endswith("reissue") else None, **form.cleaned_data)
                admin.log_change(request, enquiry, f"Issued property authorisation {new.pk}" + (f", replacing {document.pk}" if document else ""))
                return redirect(reverse("admin:realestate_realestateenquiry_ops_action", args=[enquiry.pk, "authorisation-review"]) + f"?document={new.pk}")
            elif action == "authorisation-handwritten":
                accept_authorisation(document, request.POST, method="handwritten", user=request.user)
            elif action == "authorisation-send":
                send_authorisation(document, request=request)
            elif action == "authorisation-revoke":
                with transaction.atomic():
                    locked = PropertyAuthorisation.objects.select_for_update().get(pk=document.pk)
                    locked.revoked_at = timezone.now()
                    locked.save(update_fields=["revoked_at"])
            elif action == "authorisation-scope-check":
                check = form.save(commit=False)
                check.enquiry = enquiry
                check.recorded_by = request.user
                check.save()
            else:
                return HttpResponse("Unsupported action.", status=400)
            admin.log_change(request, enquiry, f"{action}: document {document.pk if document else 'final scope check'}")
            return redirect("admin:realestate_realestateenquiry_change", enquiry.pk)
        except ValueError as exc:
            error = str(exc)
        except Exception:
            if action != "authorisation-send":
                raise
            logger.error("Property authorisation email failed for document %s", document.pk)
            error = "Email sending failed. Check the email service before retrying. Acceptance has not been recorded."
    if document:
        snapshot = document.accepted_snapshot or document.issued_snapshot
    return render(request, "admin/realestate/enquiry/authorisation_action.html", {
        **admin.admin_site.each_context(request), "opts": enquiry._meta,
        "enquiry": enquiry, "document": document, "form": form, "error": error,
        "action": action, "title": action.removeprefix("authorisation-").replace("-", " ").title() + " - Property Access & Shoot Authorisation",
        "snapshot": snapshot, "sections": document_sections(snapshot) if snapshot else [],
        "review_url": request.build_absolute_uri(reverse("property-authorisation", args=[document.token])) if document and is_available(document) else "",
    })
