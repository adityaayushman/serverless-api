"""Storage for links: a DynamoDB implementation and an in-memory one used by the tests.

Both implement the same three operations, so the service layer never touches boto3 directly.
"""

import threading


class SlugTaken(Exception):
    """Raised by ``create`` when the slug already exists."""


class DynamoLinkStore:
    """Links in a DynamoDB table keyed by ``slug``.

    Each operation is a single conditional request, so it is safe under concurrent callers
    without any locking in the application.
    """

    def __init__(self, table):
        self._table = table

    def create(self, item):
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(slug)")
        except Exception as error:  # noqa: BLE001 - botocore's ClientError is inspected, not imported
            if _error_code(error) == "ConditionalCheckFailedException":
                raise SlugTaken(item["slug"]) from error
            raise

    def get(self, slug):
        return self._table.get_item(Key={"slug": slug}).get("Item")

    def count_click(self, slug, now):
        """Atomically add one click and return the updated item.

        Returns ``None`` if the slug does not exist or has expired. Existence and expiry are checked
        in the same request as the increment, so a link cannot be counted after it expires.
        """
        try:
            response = self._table.update_item(
                Key={"slug": slug},
                UpdateExpression="ADD clicks :one",
                ConditionExpression="attribute_exists(slug) AND (attribute_not_exists(expiresAt) OR expiresAt > :now)",
                ExpressionAttributeValues={":one": 1, ":now": int(now)},
                ReturnValues="ALL_NEW",
            )
        except Exception as error:  # noqa: BLE001
            if _error_code(error) == "ConditionalCheckFailedException":
                return None
            raise
        return response.get("Attributes")


class MemoryLinkStore:
    """A thread-safe in-memory store with the same behaviour as the DynamoDB one."""

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def create(self, item):
        with self._lock:
            if item["slug"] in self._items:
                raise SlugTaken(item["slug"])
            self._items[item["slug"]] = dict(item)

    def get(self, slug):
        with self._lock:
            item = self._items.get(slug)
            return dict(item) if item else None

    def count_click(self, slug, now):
        with self._lock:
            item = self._items.get(slug)
            if item is None:
                return None
            expires_at = item.get("expiresAt")
            if expires_at is not None and not expires_at > int(now):
                return None
            item["clicks"] = int(item.get("clicks", 0)) + 1
            return dict(item)


def _error_code(error):
    response = getattr(error, "response", None) or {}
    return response.get("Error", {}).get("Code")
