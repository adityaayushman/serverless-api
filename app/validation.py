"""Input validation for the link shortener. Pure functions, no AWS imports."""

import re
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
MAX_EXPIRY_DAYS = 365

# Slugs become part of a URL path, so keep them to a safe, readable alphabet.
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

# Paths that would collide with the API's own routes.
RESERVED_SLUGS = frozenset({"links"})


class ValidationError(ValueError):
    """Raised for bad input. The message is safe to return to the caller."""


def validate_url(value):
    """Return the URL if it is an absolute http(s) URL, otherwise raise ValidationError."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("url is required")
    url = value.strip()
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError(f"url must be at most {MAX_URL_LENGTH} characters")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        raise ValidationError("url must not contain spaces or control characters")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        raise ValidationError("url is not valid") from None
    if parts.scheme not in ("http", "https"):
        raise ValidationError("url must start with http:// or https://")
    if not hostname:
        raise ValidationError("url must include a host")
    if parts.username or parts.password:
        raise ValidationError("url must not contain credentials")
    return url


def validate_slug(value):
    """Return a custom slug if it is allowed, otherwise raise ValidationError."""
    if not isinstance(value, str) or not SLUG_PATTERN.match(value):
        raise ValidationError("slug must be 3-32 characters from A-Z a-z 0-9 _ -")
    if value.lower() in RESERVED_SLUGS:
        raise ValidationError("slug is reserved")
    return value


def validate_expiry_days(value):
    """Return the number of days a link should live, or None for no expiry."""
    if value is None:
        return None
    # bool is a subclass of int, so reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("expiresInDays must be a whole number")
    if value < 1 or value > MAX_EXPIRY_DAYS:
        raise ValidationError(f"expiresInDays must be between 1 and {MAX_EXPIRY_DAYS}")
    return value
