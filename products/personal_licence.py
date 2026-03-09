from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.urls import reverse


PERSONAL_LICENCE_FILENAME = "PERSONAL USE LICENSE CERTIFICATE.md"
DEFAULT_PERSONAL_TERMS_VERSION = "PERSONAL v1.1 - March 2026"
DEFAULT_PERSONAL_TERMS_SUMMARY = (
    "Personal use only (no business, marketing, or client use).",
    "You may store the file on your personal devices and keep personal backups.",
    "You may not resell, redistribute, or upload source files to stock/POD/marketplace platforms.",
    "AI training, dataset creation, and generative model use are prohibited.",
)


def get_personal_terms_version():
    return getattr(settings, "PERSONAL_TERMS_VERSION", DEFAULT_PERSONAL_TERMS_VERSION)


def _template_roots():
    roots = []
    configured_dir = getattr(settings, "LICENCE_TEMPLATE_DIR", None)
    if configured_dir:
        roots.append(Path(configured_dir))

    base_dir = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[1]))
    roots.extend([base_dir, base_dir.parent, Path(__file__).resolve().parents[2]])

    unique_roots = []
    seen = set()
    for root in roots:
        root_str = str(root)
        if root_str in seen:
            continue
        seen.add(root_str)
        unique_roots.append(root)
    return unique_roots


@lru_cache(maxsize=1)
def resolve_personal_licence_path():
    roots = _template_roots()
    for root in roots:
        candidate = root / PERSONAL_LICENCE_FILENAME
        if candidate.exists():
            return candidate
    return roots[0] / PERSONAL_LICENCE_FILENAME


@lru_cache(maxsize=1)
def get_personal_licence_text():
    path = resolve_personal_licence_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def get_personal_licence_summary():
    summary = getattr(settings, "PERSONAL_TERMS_SUMMARY", DEFAULT_PERSONAL_TERMS_SUMMARY)
    return list(summary)


def get_personal_licence_url(request=None):
    configured = getattr(settings, "PERSONAL_TERMS_PUBLIC_URL", None)
    if configured:
        return configured

    path = reverse("personal-licence-text")
    if request is not None:
        return request.build_absolute_uri(path)
    return path
