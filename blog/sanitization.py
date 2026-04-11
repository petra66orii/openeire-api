import re
from urllib.parse import urlparse

import bleach
from bleach.css_sanitizer import CSSSanitizer
from django.conf import settings


SCRIPT_STYLE_BLOCK_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

BLOG_ALLOWED_TAGS = [
    "p",
    "div",
    "span",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "s",
    "sub",
    "sup",
    "ul",
    "ol",
    "li",
    "blockquote",
    "a",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "code",
    "pre",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
]

BLOG_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel", "target"],
    "p": ["style"],
    "div": ["style"],
    "span": ["style"],
    "blockquote": ["style"],
    "ul": ["style"],
    "ol": ["style"],
    "li": ["style"],
    "h1": ["style"],
    "h2": ["style"],
    "h3": ["style"],
    "h4": ["style"],
    "h5": ["style"],
    "h6": ["style"],
    "table": ["style"],
    "thead": ["style"],
    "tbody": ["style"],
    "tr": ["style"],
    "th": ["style", "colspan", "rowspan"],
    "td": ["style", "colspan", "rowspan"],
    "pre": ["style"],
    "code": ["style"],
}

BLOG_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
BLOG_ALLOWED_CSS_PROPERTIES = [
    "text-align",
    "color",
    "background-color",
    "font-weight",
    "font-style",
    "text-decoration",
    "margin-left",
    "list-style-type",
    "width",
    "height",
]


def _strip_script_style_blocks(text):
    return SCRIPT_STYLE_BLOCK_RE.sub("", text)


def _allowed_image_hosts():
    configured = getattr(settings, "BLOG_ALLOWED_IMAGE_HOSTS", [])
    if configured is None:
        entries = []
    elif isinstance(configured, str):
        entries = configured.split(",")
    elif isinstance(configured, (list, tuple, set, frozenset)):
        entries = configured
    else:
        # Fail closed on unexpected config types.
        return set()

    hosts = set()
    for host in entries:
        text = str(host or "").strip().lower()
        if "://" in text:
            parsed = urlparse(text)
            text = (parsed.hostname or "").lower()
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
    css_sanitizer = CSSSanitizer(
        allowed_css_properties=BLOG_ALLOWED_CSS_PROPERTIES
    )
    cleaned = bleach.clean(
        text,
        tags=BLOG_ALLOWED_TAGS,
        attributes=attributes,
        protocols=BLOG_ALLOWED_PROTOCOLS,
        css_sanitizer=css_sanitizer,
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
