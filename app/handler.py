"""AWS Lambda entry point for an API Gateway HTTP API (payload format 2.0).

Routes
    POST /links          create a short link (requires a bearer token)
    GET  /links/{slug}   link details and click count
    GET  /{slug}         302 redirect to the destination, counting the visit
"""

import base64
import hmac
import json
import logging
import os

from service import Conflict, LinkService, NotFound
from validation import ValidationError

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

MAX_BODY_BYTES = 4096
CREATE_FIELDS = {"url", "slug", "expiresInDays"}

_service = None


def build_service():
    """Create the production service lazily, so importing this module needs no AWS libraries."""
    global _service
    if _service is None:
        import boto3  # available in the Lambda runtime

        from store import DynamoLinkStore

        table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
        _service = LinkService(DynamoLinkStore(table))
    return _service


def lambda_handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "")
    path = event.get("rawPath", "")
    try:
        response = _route(event, method, path)
    except ValidationError as error:
        response = _json(400, {"error": str(error)})
    except Conflict as error:
        response = _json(409, {"error": str(error)})
    except NotFound as error:
        response = _json(404, {"error": str(error)})
    except Exception:  # noqa: BLE001 - never leak internals to the caller
        LOG.exception("unhandled error")
        response = _json(500, {"error": "internal error"})
    # Method and path only: bodies and tokens are never logged.
    LOG.info(json.dumps({"method": method, "path": _route_name(path), "status": response["statusCode"]}))
    return response


def _route(event, method, path):
    parts = [p for p in path.split("/") if p]

    if parts == ["links"]:
        if method != "POST":
            return _method_not_allowed("POST")
        return _create(event)
    if len(parts) == 2 and parts[0] == "links":
        if method != "GET":
            return _method_not_allowed("GET")
        return _json(200, build_service().stats(parts[1]))
    if len(parts) == 1:
        if method != "GET":
            return _method_not_allowed("GET")
        destination = build_service().resolve(parts[0])
        return {
            "statusCode": 302,
            "headers": {"Location": destination, "Cache-Control": "no-store"},
            "body": "",
        }
    return _json(404, {"error": "not found"})


def _create(event):
    if not _authorised(event):
        return _json(401, {"error": "a valid bearer token is required"})

    fields = _read_json_object(event)
    unknown = sorted(set(fields) - CREATE_FIELDS)
    if unknown:
        raise ValidationError(f'unknown field "{unknown[0]}"')

    link = build_service().create(
        fields.get("url"), slug=fields.get("slug"), expires_in_days=fields.get("expiresInDays")
    )
    domain = event.get("requestContext", {}).get("domainName")
    if domain:
        link["shortUrl"] = f"https://{domain}/{link['slug']}"
    return _json(201, link, {"Location": f"/links/{link['slug']}"})


def _authorised(event):
    """Check the bearer token in constant time. With no token configured, creation is disabled."""
    expected = os.environ.get("CREATE_TOKEN", "")
    if not expected:
        return False
    header = (event.get("headers") or {}).get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


def _read_json_object(event):
    body = event.get("body")
    if not body:
        raise ValidationError("request body is required")
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("request body is not valid base64 text") from None
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValidationError(f"request body must be at most {MAX_BODY_BYTES} bytes")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        raise ValidationError("request body is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise ValidationError("request body must be a JSON object")
    return parsed


def _route_name(path):
    """A log-safe route label that does not record the slug a visitor asked for."""
    parts = [p for p in path.split("/") if p]
    if parts == ["links"]:
        return "/links"
    if len(parts) == 2 and parts[0] == "links":
        return "/links/{slug}"
    return "/{slug}" if len(parts) == 1 else "other"


def _method_not_allowed(allow):
    return _json(405, {"error": "method not allowed"}, {"Allow": allow})


def _json(status, body, extra_headers=None):
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}
