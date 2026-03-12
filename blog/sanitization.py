import re
from urllib.parse import urlparse

import bleach
from django.conf import settings


SCRIPT_STYLE_BLOCK_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

BLOG_ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "blockquote",
    "a",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "code",
    "pre",
    "img",
]

BLOG_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel"],
}

BLOG_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _strip_script_style_blocks(text):
    return SCRIPT_STYLE_BLOCK_RE.sub("", text)


def _allowed_image_hosts():
    configured = getattr(settings, "BLOG_ALLOWED_IMAGE_HOSTS", [])
    hosts = set()
    for host in configured:
        text = str(host or "").strip().lower()
        if text:
            hosts.add(text.lstrip("."))
    return hosts


def _is_allowed_image_src(value):
    src = str(value or "").strip()
    if not src:
        return False

    parsed = urlparse(src)

    # Relative paths are allowed (for local/static media).
    if not parsed.scheme and not parsed.netloc:
        return True

    if parsed.scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False

    allowed_hosts = _allowed_image_hosts()
    return any(hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts)


def _img_attribute_filter(tag, name, value):
    if name in {"alt", "title", "width", "height"}:
        return True
    if name == "src":
        return _is_allowed_image_src(value)
    return False


def sanitize_blog_html(value):
    if value is None:
        return None
    text = _strip_script_style_blocks(str(value))
    attributes = dict(BLOG_ALLOWED_ATTRIBUTES)
    attributes["img"] = _img_attribute_filter
    cleaned = bleach.clean(
        text,
        tags=BLOG_ALLOWED_TAGS,
        attributes=attributes,
        protocols=BLOG_ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    return CONTROL_CHARS_RE.sub("", cleaned).strip()


def sanitize_blog_plain_text(value, max_len=None):
    if value is None:
        return None
    text = _strip_script_style_blocks(str(value))
    cleaned = bleach.clean(
        text,
        tags=[],
        attributes={},
        strip=True,
        strip_comments=True,
    )
    cleaned = CONTROL_CHARS_RE.sub("", cleaned).strip()
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned
