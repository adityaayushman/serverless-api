"""Business logic for the link shortener. It depends on a store, never on AWS directly."""

import secrets
import string
import time
from datetime import datetime, timezone

from store import SlugTaken
from validation import validate_expiry_days, validate_slug, validate_url

_ALPHABET = string.ascii_letters + string.digits
_SLUG_LENGTH = 7
_MAX_ATTEMPTS = 5
_SECONDS_PER_DAY = 86_400


class NotFound(Exception):
    """The slug does not exist or has expired."""


class Conflict(Exception):
    """A requested custom slug is already taken."""


def _new_slug():
    return "".join(secrets.choice(_ALPHABET) for _ in range(_SLUG_LENGTH))


class LinkService:
    def __init__(self, store, clock=time.time, slug_factory=_new_slug):
        self._store = store
        self._clock = clock
        self._slug_factory = slug_factory

    def create(self, url, slug=None, expires_in_days=None):
        """Create a short link and return its public representation."""
        url = validate_url(url)
        days = validate_expiry_days(expires_in_days)
        now = int(self._clock())

        item = {
            "url": url,
            "createdAt": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clicks": 0,
        }
        if days is not None:
            # DynamoDB's TTL feature reads this attribute as an epoch time in seconds.
            item["expiresAt"] = now + days * _SECONDS_PER_DAY

        if slug is not None:
            item["slug"] = validate_slug(slug)
            try:
                self._store.create(item)
            except SlugTaken:
                raise Conflict("slug is already taken") from None
            return _public(item)

        # Random slugs can collide, so retry a few times before giving up.
        for _ in range(_MAX_ATTEMPTS):
            item["slug"] = self._slug_factory()
            try:
                self._store.create(item)
            except SlugTaken:
                continue
            return _public(item)
        raise RuntimeError("could not allocate a unique slug")

    def resolve(self, slug):
        """Return the destination URL for a slug and count the visit."""
        item = self._store.count_click(slug, self._clock())
        if item is None:
            raise NotFound("link not found")
        return item["url"]

    def stats(self, slug):
        """Return a link's details and click count without counting a visit."""
        item = self._store.get(slug)
        if item is None or _expired(item, self._clock()):
            raise NotFound("link not found")
        return _public(item)


def _expired(item, now):
    expires_at = item.get("expiresAt")
    return expires_at is not None and not int(expires_at) > int(now)


def _public(item):
    result = {
        "slug": item["slug"],
        "url": item["url"],
        "createdAt": item["createdAt"],
        "clicks": int(item.get("clicks", 0)),
    }
    if "expiresAt" in item:
        result["expiresAt"] = datetime.fromtimestamp(int(item["expiresAt"]), timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return result
