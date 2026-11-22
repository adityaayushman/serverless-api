import base64
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import handler  # noqa: E402
from service import LinkService  # noqa: E402
from store import MemoryLinkStore  # noqa: E402

TOKEN = "s3cret-token-value"


def event(method, path, body=None, token=TOKEN, base64_body=False, headers=None):
    all_headers = dict(headers or {})
    if token is not None:
        all_headers["authorization"] = f"Bearer {token}"
    if body is not None and not isinstance(body, str):
        body = json.dumps(body)
    if body is not None and base64_body:
        body = base64.b64encode(body.encode()).decode()
    return {
        "rawPath": path,
        "headers": all_headers,
        "body": body,
        "isBase64Encoded": base64_body,
        "requestContext": {"domainName": "abc123.execute-api.eu-west-1.amazonaws.com", "http": {"method": method}},
    }


def call(method, path, **kwargs):
    response = handler.lambda_handler(event(method, path, **kwargs), None)
    body = json.loads(response["body"]) if response["body"] else None
    return response, body


class HandlerTestCase(unittest.TestCase):
    def setUp(self):
        self.store = MemoryLinkStore()
        handler._service = LinkService(self.store)
        patcher = mock.patch.dict(os.environ, {"CREATE_TOKEN": TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, handler, "_service", None)


class CreateTests(HandlerTestCase):
    def test_creates_a_link(self):
        response, body = call("POST", "/links", body={"url": "https://example.com/page"})
        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(body["url"], "https://example.com/page")
        self.assertEqual(body["shortUrl"], f"https://abc123.execute-api.eu-west-1.amazonaws.com/{body['slug']}")
        self.assertEqual(response["headers"]["Location"], f"/links/{body['slug']}")
        self.assertEqual(response["headers"]["Content-Type"], "application/json")

    def test_accepts_a_custom_slug_and_expiry(self):
        _, body = call("POST", "/links", body={"url": "https://example.com", "slug": "docs", "expiresInDays": 7})
        self.assertEqual(body["slug"], "docs")
        self.assertIn("expiresAt", body)

    def test_accepts_a_base64_encoded_body(self):
        response, _ = call("POST", "/links", body={"url": "https://example.com"}, base64_body=True)
        self.assertEqual(response["statusCode"], 201)

    def test_duplicate_custom_slug_is_a_conflict(self):
        call("POST", "/links", body={"url": "https://example.com", "slug": "docs"})
        response, body = call("POST", "/links", body={"url": "https://other.example.com", "slug": "docs"})
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(body, {"error": "slug is already taken"})


class AuthTests(HandlerTestCase):
    def test_requires_a_token(self):
        response, _ = call("POST", "/links", body={"url": "https://example.com"}, token=None)
        self.assertEqual(response["statusCode"], 401)

    def test_rejects_a_wrong_token(self):
        response, _ = call("POST", "/links", body={"url": "https://example.com"}, token="wrong")
        self.assertEqual(response["statusCode"], 401)

    def test_rejects_a_non_bearer_scheme(self):
        e = event("POST", "/links", body={"url": "https://example.com"}, token=None, headers={"authorization": f"Basic {TOKEN}"})
        self.assertEqual(handler.lambda_handler(e, None)["statusCode"], 401)

    def test_rejects_an_empty_bearer_value(self):
        e = event("POST", "/links", body={"url": "https://example.com"}, token=None, headers={"authorization": "Bearer "})
        self.assertEqual(handler.lambda_handler(e, None)["statusCode"], 401)

    def test_accepts_the_bearer_scheme_in_any_case(self):
        e = event("POST", "/links", body={"url": "https://example.com"}, token=None, headers={"authorization": f"bEaReR {TOKEN}"})
        self.assertEqual(handler.lambda_handler(e, None)["statusCode"], 201)

    def test_creation_is_disabled_when_no_token_is_configured(self):
        with mock.patch.dict(os.environ, {"CREATE_TOKEN": ""}):
            response, _ = call("POST", "/links", body={"url": "https://example.com"}, token="")
        self.assertEqual(response["statusCode"], 401)

    def test_reads_are_public(self):
        _, created = call("POST", "/links", body={"url": "https://example.com"})
        self.assertEqual(call("GET", f"/links/{created['slug']}", token=None)[0]["statusCode"], 200)
        self.assertEqual(call("GET", f"/{created['slug']}", token=None)[0]["statusCode"], 302)


class BodyValidationTests(HandlerTestCase):
    def assert_bad_request(self, body, fragment, **kwargs):
        response, parsed = call("POST", "/links", body=body, **kwargs)
        self.assertEqual(response["statusCode"], 400, parsed)
        self.assertIn(fragment, parsed["error"])

    def test_body_is_required(self):
        self.assert_bad_request(None, "body is required")
        self.assert_bad_request("", "body is required")

    def test_body_must_be_json(self):
        self.assert_bad_request("not json", "not valid JSON")

    def test_body_must_be_an_object(self):
        self.assert_bad_request("[1, 2]", "must be a JSON object")
        self.assert_bad_request('"text"', "must be a JSON object")

    def test_rejects_invalid_base64(self):
        e = event("POST", "/links", body="@@@not-base64@@@")
        e["isBase64Encoded"] = True
        response = handler.lambda_handler(e, None)
        self.assertEqual(response["statusCode"], 400)
        self.assertIn("base64", json.loads(response["body"])["error"])

    def test_rejects_oversized_bodies(self):
        self.assert_bad_request({"url": "https://example.com/" + "a" * handler.MAX_BODY_BYTES}, "at most")

    def test_rejects_unknown_fields(self):
        self.assert_bad_request({"url": "https://example.com", "admin": True}, 'unknown field "admin"')

    def test_reports_validation_errors(self):
        self.assert_bad_request({"url": "javascript:alert(1)"}, "http")
        self.assert_bad_request({"slug": "abc"}, "url is required")
        self.assert_bad_request({"url": "https://example.com", "slug": "no"}, "slug must be")
        self.assert_bad_request({"url": "https://example.com", "slug": "links"}, "reserved")
        self.assert_bad_request({"url": "https://example.com", "expiresInDays": "7"}, "whole number")


class ReadTests(HandlerTestCase):
    def test_redirects_and_counts_the_visit(self):
        _, created = call("POST", "/links", body={"url": "https://example.com/target"})
        response, _ = call("GET", f"/{created['slug']}", token=None)
        self.assertEqual(response["statusCode"], 302)
        self.assertEqual(response["headers"]["Location"], "https://example.com/target")
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")
        self.assertEqual(call("GET", f"/links/{created['slug']}")[1]["clicks"], 1)

    def test_stats_do_not_count_a_visit(self):
        _, created = call("POST", "/links", body={"url": "https://example.com"})
        call("GET", f"/links/{created['slug']}")
        self.assertEqual(call("GET", f"/links/{created['slug']}")[1]["clicks"], 0)

    def test_unknown_slugs_are_not_found(self):
        self.assertEqual(call("GET", "/missing")[0]["statusCode"], 404)
        self.assertEqual(call("GET", "/links/missing")[0]["statusCode"], 404)

    def test_unknown_routes_are_not_found(self):
        self.assertEqual(call("GET", "/")[0]["statusCode"], 404)
        self.assertEqual(call("GET", "/a/b/c")[0]["statusCode"], 404)


class MethodTests(HandlerTestCase):
    def test_wrong_methods_get_405_with_an_allow_header(self):
        for method, path, allow in (
            ("GET", "/links", "POST"),
            ("DELETE", "/links/abc", "GET"),
            ("POST", "/abc", "GET"),
        ):
            with self.subTest(method=method, path=path):
                response, _ = call(method, path)
                self.assertEqual(response["statusCode"], 405)
                self.assertEqual(response["headers"]["Allow"], allow)


class FailureTests(HandlerTestCase):
    def test_unexpected_errors_do_not_leak_details(self):
        with mock.patch.object(handler._service, "resolve", side_effect=RuntimeError("table arn:aws:dynamodb:secret")):
            with self.assertLogs(level="ERROR"):
                response, body = call("GET", "/abc")
        self.assertEqual(response["statusCode"], 500)
        self.assertEqual(body, {"error": "internal error"})
        self.assertNotIn("arn:aws", response["body"])

    def test_logs_contain_neither_tokens_nor_slugs(self):
        with self.assertLogs(level="INFO") as logs:
            _, created = call("POST", "/links", body={"url": "https://example.com", "slug": "private-slug"})
            call("GET", "/private-slug", token=None)
            call("GET", "/links/private-slug")
        output = "\n".join(logs.output)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("private-slug", output)
        self.assertNotIn("example.com", output)
        self.assertIn('"path": "/{slug}"', output)


class BuildServiceTests(unittest.TestCase):
    def test_builds_a_dynamodb_backed_service_from_the_environment_once(self):
        handler._service = None
        fake_table = object()
        fake_boto3 = mock.Mock()
        fake_boto3.resource.return_value.Table.return_value = fake_table
        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}), mock.patch.dict(os.environ, {"TABLE_NAME": "links-table"}):
            first = handler.build_service()
            second = handler.build_service()
        fake_boto3.resource.assert_called_once_with("dynamodb")
        fake_boto3.resource.return_value.Table.assert_called_once_with("links-table")
        self.assertIs(first, second)
        handler._service = None


if __name__ == "__main__":
    unittest.main()
