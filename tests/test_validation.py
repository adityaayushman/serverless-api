import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from validation import (  # noqa: E402
    MAX_EXPIRY_DAYS,
    MAX_URL_LENGTH,
    ValidationError,
    validate_expiry_days,
    validate_slug,
    validate_url,
)


class ValidateUrlTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertEqual(validate_url("https://example.com/path?q=1#frag"), "https://example.com/path?q=1#frag")
        self.assertEqual(validate_url("http://example.com"), "http://example.com")

    def test_trims_surrounding_whitespace(self):
        self.assertEqual(validate_url("  https://example.com  "), "https://example.com")

    def test_accepts_ports_and_ip_hosts(self):
        self.assertEqual(validate_url("http://127.0.0.1:8080/x"), "http://127.0.0.1:8080/x")

    def test_rejects_missing_or_non_string_values(self):
        for bad in (None, "", "   ", 42, ["https://example.com"], {"url": "x"}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_url(bad)

    def test_rejects_other_schemes(self):
        for bad in ("javascript:alert(1)", "ftp://example.com", "file:///etc/passwd", "data:text/html,hi", "//example.com"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_url(bad)

    def test_rejects_urls_without_a_host(self):
        for bad in ("https://", "https:///path", "http://:8080"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_url(bad)

    def test_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValidationError, "credentials"):
            validate_url("https://user:secret@example.com")
        with self.assertRaisesRegex(ValidationError, "credentials"):
            validate_url("https://user@example.com")

    def test_rejects_spaces_and_control_characters(self):
        for bad in ("https://exa mple.com", "https://example.com/a\nb", "https://example.com/\x00", "https://example.com/\x7f"):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValidationError):
                validate_url(bad)

    def test_rejects_overlong_urls(self):
        url = "https://example.com/" + "a" * MAX_URL_LENGTH
        with self.assertRaisesRegex(ValidationError, "at most"):
            validate_url(url)

    def test_accepts_a_url_at_the_length_limit(self):
        url = "https://example.com/" + "a" * (MAX_URL_LENGTH - len("https://example.com/"))
        self.assertEqual(validate_url(url), url)

    def test_rejects_unparseable_hosts(self):
        with self.assertRaises(ValidationError):
            validate_url("http://[invalid")


class ValidateSlugTests(unittest.TestCase):
    def test_accepts_the_allowed_alphabet(self):
        for good in ("abc", "My-Link_1", "a" * 32, "0000"):
            with self.subTest(good=good):
                self.assertEqual(validate_slug(good), good)

    def test_rejects_bad_slugs(self):
        for bad in (None, "", "ab", "a" * 33, "has space", "slash/inside", "dot.dot", "emoji☃", 123):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_slug(bad)

    def test_rejects_reserved_slugs_in_any_case(self):
        for reserved in ("links", "LINKS", "Links"):
            with self.subTest(reserved=reserved), self.assertRaisesRegex(ValidationError, "reserved"):
                validate_slug(reserved)


class ValidateExpiryTests(unittest.TestCase):
    def test_none_means_no_expiry(self):
        self.assertIsNone(validate_expiry_days(None))

    def test_accepts_the_valid_range(self):
        self.assertEqual(validate_expiry_days(1), 1)
        self.assertEqual(validate_expiry_days(MAX_EXPIRY_DAYS), MAX_EXPIRY_DAYS)

    def test_rejects_out_of_range_and_wrong_types(self):
        for bad in (0, -1, MAX_EXPIRY_DAYS + 1, 1.5, "7", True, False, [], {}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_expiry_days(bad)


if __name__ == "__main__":
    unittest.main()
