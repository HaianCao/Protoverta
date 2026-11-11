import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from http.client import HTTPConnection, HTTPResponse
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
from test_gateway_capabilities import listener, post

from protoverta.model_snapshot import MAX_BYTES, ModelSnapshot, SnapshotModel
from protoverta.server import GatewayConfig
from protoverta.session_diagnostics import diagnostic_journal, latest_diagnostics

BASE = "https://private.example.test/team/v1"


def snapshot(*ids):
    return ModelSnapshot(BASE, "chat", tuple(SnapshotModel(m, "Display " + m) for m in ids))


def unavailable(*args, **kwargs):
    pytest.fail("Model discovery must not dispatch upstream")


def get(url, query="", token="local"):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    return urlopen(Request(url.replace("/responses", "/models") + query, headers=headers), timeout=3)


def test_auth_bounded_paging_and_no_upstream_or_private_metadata():
    records = []
    config = GatewayConfig("chat", BASE, "secret-key", "local", model_snapshot=snapshot("vendor/a:free", "b", "c"))
    with listener(config, unavailable, observer=records.append) as url:
        for token in (None, "wrong"):
            with pytest.raises(HTTPError) as exc:
                get(url, token=token)
            assert exc.value.code == 401
            assert "secret-key" not in exc.value.read().decode()
        with get(url, "?limit=2") as response:
            assert response.headers["Cache-Control"] == "no-store"
            wire = response.read()
        first = json.loads(wire)
        assert first["object"] == "list" and first["has_more"] is True
        assert first["last_id"] == "b"
        assert [m["id"] for m in first["data"]] == ["vendor/a:free", "b"]
        assert first["data"][0]["display_name"] == "Display vendor/a:free"
        assert first["data"][0]["capability_status"] == "unknown"
        assert set(first["data"][0]) == {"id", "object", "display_name", "capability_status"}
        with get(url, "?limit=2&after_id=b") as response:
            last = json.load(response)
        assert last["has_more"] is False and [m["id"] for m in last["data"]] == ["c"]
        with get(url, "?after_id=c") as response:
            assert json.load(response)["data"] == []
        assert all(
            text.encode() not in wire for text in (BASE, "secret-key", "local", "context_window", "owned_by", "created")
        )
    assert all(record.protocol == "models" for record in records)
    assert all(record.requested_model_digest is None and record.input_tokens is None for record in records)


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?limit=1001",
        "?limit=-1",
        "?limit=01",
        "?limit=1.0",
        "?limit=",
        "?limit=true",
        "?limit=1&limit=2",
        "?unknown=x",
        "?after_id=missing",
        "?after_id=",
        "?limit",
        "?after_id=%zz",
        "?after_id=%ff",
        "?limit=1&after_id=b&third=x",
        "?x=" + "a" * 2048,
    ],
)
def test_bad_query_is_bounded_safe_error(query):
    config = GatewayConfig("chat", BASE, "secret-key", "local", model_snapshot=snapshot("a", "b"))
    with listener(config, unavailable) as url:
        with pytest.raises(HTTPError) as exc:
            get(url, query)
        assert exc.value.code == 400
        assert BASE not in exc.value.read().decode()
        with get(url) as response:
            assert len(json.load(response)["data"]) == 2


def test_missing_snapshot_and_explicit_empty_are_distinct():
    for value, status in ((None, 404), (snapshot(), 200)):
        config = GatewayConfig("chat", BASE, "key", "local", model_snapshot=value)
        with listener(config, unavailable) as url:
            if status == 404:
                with pytest.raises(HTTPError) as exc:
                    get(url)
                assert exc.value.code == status
            else:
                with get(url) as response:
                    assert json.load(response) == {
                        "object": "list",
                        "data": [],
                        "has_more": False,
                        "first_id": None,
                        "last_id": None,
                    }


def test_snapshot_identity_requires_exact_prefix_and_protocol():
    for base, protocol in (
        ("https://other.example.test/team/v1", "chat"),
        (BASE + "/other", "chat"),
        (BASE, "messages"),
    ):
        with pytest.raises(ValueError, match="exact upstream"):
            GatewayConfig(protocol, base, "key", "local", model_snapshot=snapshot("a"))
    assert snapshot("a").matches(BASE + "/", "chat")


@pytest.mark.parametrize(
    "identifier,name",
    [
        ("", "name"),
        ("has space", "name"),
        ("a", ""),
        ("a\n", "name"),
        ("a", "name\x7f"),
        ("a", "\ud800"),
        ("\udfff", "name"),
        ("a" * 513, "name"),
        ("a", "n" * 513),
    ],
)
def test_invalid_entries_rejected_without_reflecting_content(identifier, name):
    with pytest.raises(ValueError, match="invalid snapshot"):
        SnapshotModel(identifier, name)


def test_immutable_size_and_duplicate_limits():
    model = SnapshotModel("a", "display")
    with pytest.raises(FrozenInstanceError):
        model.id = "changed"
    with pytest.raises(ValueError, match="immutable"):
        ModelSnapshot(BASE, "chat", [model])
    with pytest.raises(ValueError, match="unique"):
        ModelSnapshot(BASE, "chat", (model, model))
    with pytest.raises(ValueError, match="10000"):
        ModelSnapshot(BASE, "chat", (model,) * 10001)
    with pytest.raises(ValueError, match="2 MiB"):
        ModelSnapshot(BASE, "chat", tuple(SnapshotModel(str(i), "n" * 512) for i in range(5000)))
    assert MAX_BYTES == 2 * 1024 * 1024


def test_cursors_preserve_exact_ids_and_no_capability_inference():
    value = snapshot("vendor/a:free", "组织/模型", "third")
    page = value.page(urlencode({"after_id": "组织/模型", "limit": 1}))
    assert page["data"][0]["id"] == "third"
    assert value.models[0].id == "vendor/a:free"
    page["data"][0]["id"] = "changed"
    assert value.models[-1].id == "third"


def test_explicit_id_constructor_is_bounded_and_deduplicates():
    value = ModelSnapshot.from_ids(BASE, "chat", ("a", "b", "a"))
    assert [(m.id, m.display_name) for m in value.models] == [("a", "a"), ("b", "b")]
    with pytest.raises(ValueError):
        ModelSnapshot.from_ids(BASE, "chat", (["unhashable"],))
    with pytest.raises(ValueError):
        ModelSnapshot.from_ids(BASE, "chat", ["mutable"])


def test_concurrent_gateways_keep_snapshots_and_tokens_isolated():
    configs = [
        GatewayConfig("chat", BASE, "key", token, model_snapshot=snapshot(model))
        for token, model in (("alpha", "A"), ("beta", "B"))
    ]
    with listener(configs[0], unavailable) as a, listener(configs[1], unavailable) as b:

        def read(pair):
            url, token, expected = pair
            with get(url, token=token) as response:
                return json.load(response)["data"][0]["id"] == expected

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(pool.map(read, [(a, "alpha", "A"), (b, "beta", "B")] * 5))
        with pytest.raises(HTTPError) as exc:
            get(a, token="beta")
        assert exc.value.code == 401


def test_duplicate_auth_and_malformed_target_are_safe_errors():
    config = GatewayConfig("chat", BASE, "key", "local", model_snapshot=snapshot("a"))
    with listener(config, unavailable) as url:
        host = url.removeprefix("http://").split("/")[0]
        connection = HTTPConnection(host, timeout=3)
        connection.putrequest("GET", "/v1/models")
        connection.putheader("Authorization", "Bearer local")
        connection.putheader("Authorization", "Bearer local")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        connection.close()
        connection = HTTPConnection(host, timeout=3)
        connection.connect()
        try:
            connection.sock.sendall(b"GET http://[ HTTP/1.0\r\nHost: localhost\r\nAuthorization: Bearer local\r\n\r\n")
            with HTTPResponse(connection.sock) as response:
                response.begin()
                assert response.status == 400
                response.read()
        finally:
            connection.close()
        with get(url) as response:
            assert response.status == 200


def test_model_list_observation_roundtrips_private_journal(tmp_path):
    config = GatewayConfig("chat", BASE, "key", "local", model_snapshot=snapshot("a"))
    with (
        diagnostic_journal(tmp_path) as sink,
        listener(config, unavailable, observer=sink) as url,
        get(url) as response,
    ):
        response.read()
    records = latest_diagnostics(tmp_path)["records"]
    assert len(records) == 1 and records[0]["protocol"] == "models"
    assert records[0]["outcome"] == "success" and records[0]["input_tokens"] is None


def test_default_page_is_bounded_and_continuation_reaches_remainder():
    value = ModelSnapshot.from_ids(BASE, "chat", tuple(str(i) for i in range(1001)))
    page = value.page("")
    assert len(page["data"]) == 1000 and page["has_more"] is True
    assert [m["id"] for m in value.page("after_id=999")["data"]] == ["1000"]


def test_catalog_obeys_local_admission_while_inference_is_active():
    import io

    entered, release = threading.Event(), threading.Event()

    def upstream(request, *, timeout):
        entered.set()
        assert release.wait(4)
        return io.BytesIO(b'{"id":"r","object":"response","output":[]}')

    config = GatewayConfig(
        "responses",
        BASE,
        "key",
        "local",
        max_concurrent_requests=1,
        model_snapshot=ModelSnapshot.from_ids(BASE, "responses", ("a",)),
    )
    with listener(config, upstream) as url, ThreadPoolExecutor(max_workers=1) as pool:

        def infer():
            with post(url, {"model": "a", "input": "hi"}) as response:
                response.read()
                return response.status

        pending = pool.submit(infer)
        try:
            assert entered.wait(2)
            with pytest.raises(HTTPError) as exc:
                get(url)
            assert exc.value.code == 503 and exc.value.headers["Retry-After"] == "1"
            exc.value.read()
        finally:
            release.set()
        assert pending.result(3) == 200
        with get(url) as response:
            assert response.status == 200


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Authorization": "Bearer local", "x-api-key": "local"}, 200),
        ({"x-api-key": "local"}, 200),
        ({"Authorization": "Bearer local", "x-api-key": "other"}, 401),
        ({"Authorization": "Bearer other", "x-api-key": "local"}, 401),
        ({"Authorization": "Basic local"}, 401),
    ],
)
def test_model_discovery_accepts_both_credential_headers_when_all_match(headers, status):
    # Claude Code 2.1.248+ sends both headers when both resolve.
    config = GatewayConfig("chat", BASE, "secret-key", "local", model_snapshot=snapshot("a"))
    with listener(config, unavailable) as url:
        request = Request(url.replace("/responses", "/models"), headers=headers)
        if status == 200:
            with urlopen(request, timeout=3) as response:
                assert response.status == 200
        else:
            with pytest.raises(HTTPError) as exc:
                urlopen(request, timeout=3)
            assert exc.value.code == status
