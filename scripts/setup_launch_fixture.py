"""Synthetic foreground CLI for browser handoff tests; never invoke user tools."""

import io
import json
import socket
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

CHILD = r'''
import json, os, sys, time
from pathlib import Path
from urllib.request import Request, ProxyHandler, build_opener
root = Path(os.environ["PROTOVERTA_SETUP_FIXTURE_ROOT"])
assert "manual-model" in sys.argv
assert not os.environ.get("FIXTURE_TOKEN"), "Upstream credential leaked into child"
if "CODEX_CA_GATEWAY_TOKEN" in os.environ:
    base = json.loads(next(arg.split("=", 1)[1] for arg in sys.argv if ".base_url=" in arg))
    token = os.environ["CODEX_CA_GATEWAY_TOKEN"]
    path, body = "/responses", {"model": "manual-model", "input": "fixture"}
else:
    base = os.environ["ANTHROPIC_BASE_URL"] + "/v1"
    token = os.environ["ANTHROPIC_AUTH_TOKEN"]
    path, body = "/messages", {"model": "manual-model", "max_tokens": 16, "messages": [{"role": "user", "content": "fixture"}]}
request = Request(base + path, data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
    assert b"fixture-answer" in response.read()
(root / "child-ready").write_text("gateway round trip passed", encoding="utf-8")
until = time.monotonic() + 12
while not (root / "release-child").exists():
    if time.monotonic() > until:
        raise RuntimeError("Synthetic terminal was not released")
    time.sleep(0.01)
(root / "child-finished").write_text("ok", encoding="utf-8")
raise SystemExit(7)
'''


class SetupLaunchFixture:
    def __init__(self, root, web, launcher):
        self.root = Path(root)
        self.original_launch = web.launch
        self.launcher = launcher
        self.calls = []
        self.exit_codes = []
        self.child = self.root / "synthetic_cli.py"
        version = "codex-cli 0.166.0" if launcher.__name__.startswith("codex_adapter") else "2.1.308 (Claude Code)"
        version_query = f"import sys\nif sys.argv[1:] == ['--version']:\n    print({version!r})\n    raise SystemExit(0)\n"
        self.child.write_text(version_query + CHILD, encoding="utf-8")

    def launch(self, *args):
        from protoverta import server

        self.calls.append(True)
        original_handler = server.make_handler
        response = {"model": "manual-model", "choices": [{"message": {"role": "assistant", "content": "fixture-answer"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

        def make_handler(config, *, observer=None, upstream_open=None):
            return original_handler(config, upstream_open=lambda *_args, **_kwargs: io.BytesIO(json.dumps(response).encode()), observer=observer)

        with ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"PROTOVERTA_SETUP_FIXTURE_ROOT": str(self.root)}))
            stack.enter_context(patch.object(self.launcher.shutil, "which", return_value=sys.executable))
            stack.enter_context(patch.object(self.launcher, "command_prefix", return_value=[sys.executable, str(self.child)]))
            stack.enter_context(patch.object(server, "make_handler", make_handler))
            result = self.original_launch(*args)
        self.exit_codes.append(result)
        return result

    def release_after_ready(self, setup_url, worker):
        from urllib.parse import urlsplit

        until = time.monotonic() + 10
        while not (self.root / "child-ready").exists() and worker.is_alive() and time.monotonic() < until:
            time.sleep(0.01)
        assert (self.root / "child-ready").is_file(), "Foreground child did not complete the gateway round trip"
        assert worker.is_alive(), "Setup returned before its foreground CLI exited"
        endpoint = urlsplit(setup_url)
        try:
            with socket.create_connection((endpoint.hostname, endpoint.port), timeout=1):
                raise AssertionError("Setup listener remained open while CLI was running")
        except OSError:
            pass
        self.release()

    def release(self):
        (self.root / "release-child").touch()
