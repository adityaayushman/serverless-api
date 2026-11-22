import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service import Conflict, LinkService, NotFound  # noqa: E402
from store import DynamoLinkStore, MemoryLinkStore, SlugTaken  # noqa: E402
from validation import ValidationError  # noqa: E402

NOW = 1_800_000_000  # 2027-01-15T08:00:00Z


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def make_service(slugs=None, clock=None):
    store = MemoryLinkStore()
    factory = None
    if slugs is not None:
        iterator = iter(slugs)
        factory = lambda: next(iterator)  # noqa: E731
    kwargs = {"clock": clock or Clock()}
    if factory:
        kwargs["slug_factory"] = factory
    return LinkService(store, **kwargs), store


class CreateTests(unittest.TestCase):
    def test_creates_a_link_with_a_generated_slug(self):
        service, _ = make_service()
        link = service.create("https://example.com/page")
        self.assertEqual(len(link["slug"]), 7)
        self.assertTrue(link["slug"].isalnum())
        self.assertEqual(link["url"], "https://example.com/page")
        self.assertEqual(link["clicks"], 0)
        self.assertEqual(link["createdAt"], "2027-01-15T08:00:00Z")
        self.assertNotIn("expiresAt", link)

    def test_creates_a_link_with_a_custom_slug(self):
        service, _ = make_service()
        self.assertEqual(service.create("https://example.com", slug="my-link")["slug"], "my-link")

    def test_custom_slug_conflict(self):
        service, _ = make_service()
        service.create("https://example.com", slug="taken")
        with self.assertRaises(Conflict):
            service.create("https://other.example.com", slug="taken")

    def test_retries_when_a_random_slug_collides(self):
        service, _ = make_service(slugs=["aaaaaaa", "aaaaaaa", "bbbbbbb"])
        self.assertEqual(service.create("https://example.com/1")["slug"], "aaaaaaa")
        self.assertEqual(service.create("https://example.com/2")["slug"], "bbbbbbb")

    def test_gives_up_after_repeated_collisions(self):
        service, _ = make_service(slugs=["aaaaaaa"] * 20)
        service.create("https://example.com/1")
        with self.assertRaisesRegex(RuntimeError, "unique slug"):
            service.create("https://example.com/2")

    def test_sets_an_expiry_time(self):
        service, _ = make_service()
        link = service.create("https://example.com", expires_in_days=2)
        self.assertEqual(link["expiresAt"], "2027-01-17T08:00:00Z")

    def test_validates_input_and_stores_nothing(self):
        service, store = make_service()
        with self.assertRaises(ValidationError):
            service.create("javascript:alert(1)")
        with self.assertRaises(ValidationError):
            service.create("https://example.com", slug="x")
        with self.assertRaises(ValidationError):
            service.create("https://example.com", expires_in_days=0)
        self.assertIsNone(store.get("x"))


class ResolveTests(unittest.TestCase):
    def test_returns_the_destination_and_counts_visits(self):
        service, _ = make_service()
        slug = service.create("https://example.com/a")["slug"]
        self.assertEqual(service.resolve(slug), "https://example.com/a")
        self.assertEqual(service.resolve(slug), "https://example.com/a")
        self.assertEqual(service.stats(slug)["clicks"], 2)

    def test_unknown_slug(self):
        service, _ = make_service()
        with self.assertRaises(NotFound):
            service.resolve("nope")

    def test_expired_links_are_gone_and_not_counted(self):
        clock = Clock()
        service, store = make_service(clock=clock)
        slug = service.create("https://example.com", expires_in_days=1)["slug"]

        clock.now = NOW + 86_399
        self.assertEqual(service.resolve(slug), "https://example.com")

        clock.now = NOW + 86_400  # the exact expiry second is already expired
        with self.assertRaises(NotFound):
            service.resolve(slug)
        with self.assertRaises(NotFound):
            service.stats(slug)
        self.assertEqual(store.get(slug)["clicks"], 1)

    def test_links_without_expiry_never_expire(self):
        clock = Clock()
        service, _ = make_service(clock=clock)
        slug = service.create("https://example.com")["slug"]
        clock.now = NOW + 10 * 365 * 86_400
        self.assertEqual(service.resolve(slug), "https://example.com")

    def test_concurrent_visits_are_all_counted(self):
        service, _ = make_service()
        slug = service.create("https://example.com")["slug"]
        threads = [threading.Thread(target=lambda: [service.resolve(slug) for _ in range(50)]) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(service.stats(slug)["clicks"], 400)


class StatsTests(unittest.TestCase):
    def test_does_not_count_a_visit(self):
        service, _ = make_service()
        slug = service.create("https://example.com")["slug"]
        service.stats(slug)
        service.stats(slug)
        self.assertEqual(service.stats(slug)["clicks"], 0)

    def test_unknown_slug(self):
        service, _ = make_service()
        with self.assertRaises(NotFound):
            service.stats("nope")


class FakeClientError(Exception):
    """Looks like botocore's ClientError: it carries a ``response`` with an error code."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeTable:
    """Records the requests the DynamoDB adapter makes and replays a scripted outcome."""

    def __init__(self, error=None, response=None):
        self.error = error
        self.response = response or {}
        self.calls = []

    def put_item(self, **kwargs):
        self.calls.append(("put_item", kwargs))
        if self.error:
            raise self.error

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return self.response

    def update_item(self, **kwargs):
        self.calls.append(("update_item", kwargs))
        if self.error:
            raise self.error
        return self.response


class DynamoAdapterTests(unittest.TestCase):
    def test_create_is_conditional_on_the_slug_not_existing(self):
        table = FakeTable()
        DynamoLinkStore(table).create({"slug": "abc", "url": "https://example.com"})
        name, kwargs = table.calls[0]
        self.assertEqual(name, "put_item")
        self.assertEqual(kwargs["ConditionExpression"], "attribute_not_exists(slug)")

    def test_create_translates_a_failed_condition_into_slug_taken(self):
        table = FakeTable(error=FakeClientError("ConditionalCheckFailedException"))
        with self.assertRaises(SlugTaken):
            DynamoLinkStore(table).create({"slug": "abc"})

    def test_create_does_not_swallow_other_errors(self):
        table = FakeTable(error=FakeClientError("ProvisionedThroughputExceededException"))
        with self.assertRaises(FakeClientError):
            DynamoLinkStore(table).create({"slug": "abc"})

    def test_get_returns_the_item_or_none(self):
        self.assertEqual(DynamoLinkStore(FakeTable(response={"Item": {"slug": "abc"}})).get("abc"), {"slug": "abc"})
        self.assertIsNone(DynamoLinkStore(FakeTable(response={})).get("abc"))

    def test_count_click_is_one_atomic_conditional_update(self):
        table = FakeTable(response={"Attributes": {"slug": "abc", "clicks": 3}})
        item = DynamoLinkStore(table).count_click("abc", NOW)
        self.assertEqual(item["clicks"], 3)
        _, kwargs = table.calls[0]
        self.assertEqual(kwargs["UpdateExpression"], "ADD clicks :one")
        self.assertIn("attribute_exists(slug)", kwargs["ConditionExpression"])
        self.assertIn("expiresAt > :now", kwargs["ConditionExpression"])
        self.assertEqual(kwargs["ExpressionAttributeValues"], {":one": 1, ":now": NOW})
        self.assertEqual(kwargs["ReturnValues"], "ALL_NEW")

    def test_count_click_returns_none_when_the_condition_fails(self):
        table = FakeTable(error=FakeClientError("ConditionalCheckFailedException"))
        self.assertIsNone(DynamoLinkStore(table).count_click("abc", NOW))

    def test_count_click_does_not_swallow_other_errors(self):
        table = FakeTable(error=FakeClientError("InternalServerError"))
        with self.assertRaises(FakeClientError):
            DynamoLinkStore(table).count_click("abc", NOW)

    def test_errors_without_a_response_are_not_mistaken_for_conflicts(self):
        table = FakeTable(error=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            DynamoLinkStore(table).create({"slug": "abc"})


if __name__ == "__main__":
    unittest.main()
