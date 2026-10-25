"""Bounded model catalogs. Reported metadata is NEVER capability certification."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .capabilities import _prefix
from .resources import RequestBudget, positive_number
from .transport import DeadlineHTTPHandler, DeadlineHTTPSHandler


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogPolicy:
    pagination: str = "none"
    max_pages: int = 100
    max_models: int = 10000
    max_bytes: int = 8 * 1024 * 1024
    timeout: float = 30.0
    path: str = "/models"
    name_field: str = "display_name"
    context_field: str | None = None
    output_field: str | None = None
    modalities_field: str | None = None
    collection_field: str | None = "data"
    id_field: str = "id"
    query_params: tuple[tuple[str, str], ...] = ()
    page_param: str = "page"
    total_pages_field: str = "pagination.total_pages"
    first_page: int = 1
    current_page_field: str | None = None

    def __post_init__(self):
        if self.pagination not in ("none", "after_id", "numbered") or not positive_number(self.timeout) or self.timeout > 120:
            raise ValueError("invalid catalog pagination/deadline")
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in ((self.max_pages, 100), (self.max_models, 100000), (self.max_bytes, 32 * 1024 * 1024))):
            raise ValueError("catalog resource limits exceed supported bounds")
        if not isinstance(self.path, str) or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+", self.path):
            raise ValueError("catalog path must be a simple relative endpoint, without query or host")
        if any(not isinstance(value, str) or not value for value in (self.name_field, self.id_field, self.page_param, self.total_pages_field)):
            raise ValueError("required catalog field paths must be nonempty strings")
        for field in (self.name_field, self.context_field, self.output_field, self.modalities_field, self.collection_field, self.id_field, self.total_pages_field, self.page_param, self.current_page_field):
            if field is not None and (not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,7}", field)):
                raise ValueError("invalid explicit catalog metadata field path")
        if type(self.first_page) is not int or not 0 <= self.first_page <= 1:
            raise ValueError("first catalog page must be zero or one")
        if not isinstance(self.query_params, (list, tuple)) or len(self.query_params) > 16:
            raise ValueError("catalog query parameters must be bounded pairs")
        pairs = []
        for pair in self.query_params:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(not isinstance(v, str) or len(v) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in pair):
                raise ValueError("invalid catalog query parameter")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pair[0]) or pair[0] in {"after_id", self.page_param}:
                raise ValueError("query cannot override pagination")
            pairs.append(tuple(pair))
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate catalog query parameter")
        object.__setattr__(self, "query_params", tuple(pairs))

    @classmethod
    def load(cls, path, base_url):
        with Path(path).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise CatalogError("catalog policy exceeds 64 KiB")
        body = json.loads(raw)
        required = {"schema_version", "base_url", "checked_at", "evidence", "policy"}
        if not isinstance(body, dict) or set(body) != required or type(body["schema_version"]) is not int or body["schema_version"] != 1 or _prefix(body["base_url"]) != _prefix(base_url):
            raise CatalogError("catalog policy must match the exact API prefix")
        from datetime import date

        date.fromisoformat(body["checked_at"])
        if not isinstance(body["evidence"], str) or not body["evidence"].strip() or not isinstance(body["policy"], dict):
            raise CatalogError("catalog mapping requires explicit evidence and policy")
        try:
            return cls(**body["policy"])
        except TypeError as exc:
            raise CatalogError("unknown catalog policy field") from exc


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    input_modalities: tuple[str, ...] = ()
    capability_status: str = "unknown"

    def as_dict(self):
        return asdict(self)


def _field(body, path):
    value = body
    for key in path.split(".") if path else ():
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if path else None


def parse_page(body, policy):
    data = _field(body, policy.collection_field) if policy.collection_field else body
    if not isinstance(data, list):
        raise CatalogError("model catalog must contain a data array")
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise CatalogError("model catalog entries must be objects")
        identifier = _field(item, policy.id_field)
        if not isinstance(identifier, str) or not identifier or len(identifier) > 512 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in identifier):
            raise CatalogError("model catalog contains an invalid model ID")
        name = _field(item, policy.name_field)
        if name is None:
            name = identifier
        if not isinstance(name, str) or not name or len(name) > 512 or any(ord(c) < 32 for c in name):
            raise CatalogError("model catalog contains an invalid display name")
        numbers = [_field(item, field) for field in (policy.context_field, policy.output_field)]
        if any(value is not None and (type(value) is not int or value <= 0) for value in numbers):
            raise CatalogError("reported token limits must be positive integers")
        modalities = _field(item, policy.modalities_field)
        if modalities is None:
            modalities = []
        if not isinstance(modalities, list) or len(modalities) > 16 or any(not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value) for value in modalities):
            raise CatalogError("invalid reported input modalities")
        result.append(ModelInfo(identifier, name, *numbers, tuple(dict.fromkeys(modalities))))
    has_more = body.get("has_more", False) if isinstance(body, dict) else False
    if type(has_more) is not bool:
        raise CatalogError("catalog has_more must be boolean")
    if has_more and policy.pagination not in ("after_id", "numbered"):
        raise CatalogError("catalog requires an explicitly configured pagination policy")
    cursor = body.get("last_id") if has_more and policy.pagination == "after_id" else None
    if has_more and policy.pagination == "after_id" and (not isinstance(cursor, str) or not cursor or len(cursor) > 512 or any(c.isspace() for c in cursor)):
        raise CatalogError("invalid catalog pagination cursor")
    return result, cursor


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if fp is not None:
            fp.close()
        raise CatalogError("catalog redirects are not permitted")


def fetch_catalog(base_url, headers, policy=None, *, upstream_open=None):
    policy = policy or CatalogPolicy()
    if not isinstance(policy, CatalogPolicy):
        raise TypeError("expected CatalogPolicy")
    base = _prefix(base_url)
    budget = RequestBudget(min(10.0, policy.timeout), policy.timeout, policy.timeout)
    opener = upstream_open or build_opener(_NoRedirect(), DeadlineHTTPHandler(), DeadlineHTTPSHandler()).open
    models, seen, cursor, consumed = {}, set(), None, 0
    try:
        expected_total = None
        for page in range(policy.max_pages):
            query = dict(policy.query_params)
            if policy.pagination == "after_id":
                query.setdefault("limit", "1000")
            elif policy.pagination == "numbered":
                query[policy.page_param] = str(page + policy.first_page)
            if cursor is not None and policy.pagination == "after_id":
                query["after_id"] = cursor
            url = base + policy.path + ("?" + urlencode(query) if query else "")
            request = Request(url, headers=dict(headers), method="GET")
            request.protoverta_budget = budget
            with opener(request, timeout=budget.remaining()) as response:
                raw = bytearray()
                while True:
                    chunk = budget.read(response, min(4096, policy.max_bytes + 1 - consumed), streaming=True)
                    if not chunk:
                        break
                    consumed += len(chunk)
                    if consumed > policy.max_bytes:
                        raise CatalogError("catalog exceeds cumulative byte limit")
                    raw.extend(chunk)
            body = json.loads(raw)
            entries, cursor = parse_page(body, policy)
            if policy.pagination == "numbered":
                total = _field(body, policy.total_pages_field)
                if type(total) is not int or not 1 <= total <= policy.max_pages or expected_total is not None and total != expected_total:
                    raise CatalogError("invalid or changing catalog page count")
                if policy.current_page_field:
                    current = _field(body, policy.current_page_field)
                    if type(current) is not int or current != page + policy.first_page:
                        raise CatalogError("catalog did not advance to the requested page")
                if isinstance(body, dict) and "has_more" in body and body["has_more"] != (page + 1 < total):
                    raise CatalogError("conflicting catalog pagination metadata")
                expected_total = total
                cursor = str(page + 1) if page + 1 < total else None
            budget.remaining()
            for item in entries:
                budget.remaining()
                if item.id in models and models[item.id] != item:
                    raise CatalogError("conflicting model metadata across catalog pages")
                models[item.id] = item
                if len(models) > policy.max_models:
                    raise CatalogError("catalog exceeds model count limit")
            if cursor is None:
                return tuple(models[key] for key in sorted(models))
            if cursor in seen:
                raise CatalogError("catalog cursor cycle detected")
            seen.add(cursor)
        raise CatalogError("catalog exceeds page limit")
    except HTTPError as exc:
        exc.close()
        raise CatalogError(f"catalog upstream HTTP {exc.code}") from None
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError("catalog transport or JSON validation failed") from None
