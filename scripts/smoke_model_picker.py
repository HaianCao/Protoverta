"""Pinned Windows TUI model selection with local upstream and owned job cleanup."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from probe_process import run_process
from test_environment import sanitized_environment

MAIN, OTHER = "vendor/alpha:free", "vendor/beta:free"
MARKER = "P07_PICKER_OK"


class Terminal:
    def __init__(self, args, env, cwd):
        from winpty import PtyProcess

        self.process = PtyProcess.spawn(args, env=env, cwd=str(cwd), dimensions=(40, 140))
        from windows_probe_job import WindowsProbeJob

        self.job = WindowsProbeJob()
        handle = self.job.kernel.OpenProcess(0x100 | 0x1 | 0x100000, False, self.process.pid)
        if not handle:
            raise RuntimeError("Cannot acquire owned pseudo-terminal child handle")
        try:
            self.job.assign(handle)
        finally:
            self.job.kernel.CloseHandle(handle)
        self.queue = queue.Queue()
        self.raw = ""

        def read():
            try:
                while True:
                    value = self.process.read(4096)
                    if not value:
                        break
                    self.queue.put(value)
            except (EOFError, OSError):
                pass

        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def drain(self):
        while True:
            try:
                value = self.queue.get_nowait()
            except queue.Empty:
                break
            self.raw += value
            if len(self.raw) > 2 * 1024 * 1024:
                raise RuntimeError("TUI output exceeded limit")
            if "\x1b[6n" in value:
                self.process.write("\x1b[1;1R")
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", self.raw)

    def wait(self, predicate, seconds=12, start=0):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            text = self.drain()[start:]
            if predicate(text):
                return text
            time.sleep(0.05)
        raise RuntimeError("Native model picker did not reach expected boundary: " + self.drain()[-2200:])

    def send(self, text):
        chunks = text.split("\r")
        for index, chunk in enumerate(chunks):
            if chunk:
                self.process.write(chunk)
                time.sleep(0.15)
            if index < len(chunks) - 1:
                self.process.write("\r")
                time.sleep(0.15)

    def close(self):
        self.job.terminate()
        self.job.close()
        self.thread.join(1)
        try:
            self.process.close(force=True)
        except OSError:
            pass  # Both owned jobs still enforce tree cleanup.


# Native CLI releases this fixture was exercised with; extend after review.
FIXTURE_VERSIONS = {"codex": {"codex-cli 0.166.0", "codex-cli 0.167.0"}, "claude": {"2.1.308 (Claude Code)", "2.1.318 (Claude Code)", "2.1.319 (Claude Code)"}}


def worker(client, source, report, workspace):
    from protoverta.core import Protocol
    from protoverta.server import gateway_session, make_handler
    from protoverta.sse import encode_sse
    from protoverta.stream_encoder import StreamEncoder
    from protoverta.streaming import Delta

    sys.path.insert(0, str(source.resolve() / "src"))
    package = "codex_adapter" if client == "codex" else "claudapter"
    launcher = importlib.import_module(package + ".launcher")
    config_module = importlib.import_module(package + ".config")
    observed = []
    if (
        workspace is None
        or workspace.resolve().parent != Path(tempfile.gettempdir()).resolve()
        or not workspace.name.startswith("protoverta-picker-")
        or not workspace.is_dir()
        or any(workspace.iterdir())
    ):
        raise ValueError("Worker requires its parent's new empty fixture directory under Temp")
    with nullcontext(str(workspace)) as temporary:
        root = Path(temporary)
        env = sanitized_environment()
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            path = root / key
            path.mkdir()
            env[key] = str(path)
        env.update(
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
            DISABLE_AUTOUPDATER="1",
            CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
            HTTP_PROXY="http://127.0.0.1:9",
            HTTPS_PROXY="http://127.0.0.1:9",
            NO_PROXY="127.0.0.1,localhost",
            TERM="xterm-256color",
        )
        config = config_module.Config(path=root / "adapter.toml")
        version = run_process([shutil.which(client), "--version"], env=env, cwd=root, timeout=3, text=True, encoding="utf-8")
        expected_versions = FIXTURE_VERSIONS[client]
        if version.returncode or version.stdout.strip() not in expected_versions:
            raise ValueError("Model picker fixture requires its pinned native CLI version")
        provider = config_module.Provider(
            "fixture", "Synthetic provider", "http://127.0.0.1:9/v1", protocol="chat_completions" if client == "codex" else "chat", execution_mode="gateway"
        )
        provider.agent_models = {"policy": "custom", "default": OTHER, **({"effort": "low"} if client == "codex" else {})}
        config.providers["fixture"] = provider
        if client == "codex":
            config.secrets["fixture-main"] = config_module.SecretReference("fixture-main", "environment", "P07_TOKEN")
            provider.auth_secret_ref = "fixture-main"
        config_module.save(config)
        before = config.path.read_bytes()
        if client == "claude":
            (Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json").write_text(
                json.dumps(
                    {
                        "hasCompletedOnboarding": True,
                        "theme": "dark",
                        "numStartups": 1,
                        "projects": {str(root): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}},
                    }
                ),
                encoding="utf-8",
            )
        else:
            result = run_process([shutil.which("codex"), "debug", "models", "--bundled"], env=env, cwd=root, timeout=10, text=True, encoding="utf-8")
            entry = next(item for item in json.loads(result.stdout)["models"] if item["slug"] == "gpt-5.5")
            path = root / "catalog.json"
            entries = [
                {
                    **entry,
                    "slug": mid,
                    "display_name": label,
                    "priority": index,
                    "supports_search_tool": False,
                    "tool_mode": None,
                    "support_verbosity": False,
                    "apply_patch_tool_type": None,
                    "experimental_supported_tools": [],
                    "default_reasoning_summary": "none",
                }
                for index, (mid, label) in enumerate(((MAIN, "Synthetic Alpha"), (OTHER, "Synthetic Beta")))
            ]
            path.write_text(json.dumps({"models": entries}), encoding="utf-8")
            env["CODEX_CA_MODEL_CATALOG"] = str(path)

        def upstream(request, *, timeout):
            import io

            if len(observed) >= 8:
                raise RuntimeError("Picker fixture request cap reached")
            body = json.loads(request.data)
            users = [message for message in body.get("messages", []) if message.get("role") == "user"]
            user_text = " ".join(str(message.get("content")) for message in users)
            observed.append(
                {
                    "model": body.get("model"),
                    "marker_prompt": MARKER in request.data.decode(),
                    "task_prompt": "Reply with " + MARKER + "." in user_text,
                    "request_kind": "title_helper" if "Write the title in the predominant language" in user_text else "conversation",
                }
            )
            encoder = StreamEncoder(Protocol.CHAT)
            deltas = [
                Delta("start", model=body["model"], input_tokens=1, output_tokens=0),
                Delta("text", text=MARKER),
                Delta("finish", reason="stop", input_tokens=1, output_tokens=1),
            ]
            return io.BytesIO(b"".join(encode_sse(event.event, event.data) for delta in deltas for event in encoder.feed(delta)))

        token_method = "resolve_token" if client == "codex" else "get_token"
        terminal = None
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(launcher, token_method, return_value="synthetic-token"):
                gateway, _ = launcher._gateway(config, provider, MAIN)
                with (
                    patch("protoverta.server.make_handler", side_effect=lambda config, **kwargs: make_handler(config, upstream_open=upstream, **kwargs)),
                    gateway_session(gateway) as origin,
                ):
                    extra = (
                        ["--no-daemon", "--no-alt-screen", "--sandbox", "read-only"]
                        if client == "codex"
                        else ["--setting-sources", "", "--strict-mcp-config", "--tools", "", "--system-prompt", "Synthetic picker test; no tools."]
                    )
                    if client == "codex":
                        for key in ("features.daemon_auto_start", "features.plugins", "features.apps", "features.remote_plugin", "check_for_update_on_startup"):
                            extra += ["-c", key + "=false"]
                    args, child_env = launcher.build_invocation(
                        config, provider, MAIN, "low" if client == "codex" else "default", extra, gateway_url=origin, gateway_token=gateway.inbound_token
                    )
                    terminal = Terminal(args, child_env, root)
                    text = terminal.wait(lambda text: "trust" in text.lower() or MAIN in text or "Synthetic Alpha" in text or "theme" in text.lower())
                    compact = re.sub(r"\s", "", text).lower()
                    if "yes,itrustthisfolder" in compact or "yes,iproceed" in compact:
                        terminal.wait(lambda text: "entertoconfirm" in re.sub(r"\s", "", text).lower())
                        time.sleep(0.8)
                        terminal.send("\x1b[B\r")
                    elif "trust" in compact or "theme" in compact:
                        terminal.wait(lambda text: "entertoconfirm" in re.sub(r"\s", "", text).lower())
                        time.sleep(0.8)
                        terminal.send("\x1b[B\r" if client == "claude" else "\r")
                    terminal.wait(lambda text: MAIN in text or "Synthetic Alpha" in text)
                    time.sleep(0.8)

                    def native_defaults():
                        result = {}
                        for path in (
                            Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json",
                            Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json",
                            Path(env["HOME"]) / ".claude.json",
                        ):
                            try:
                                result[path.name] = json.loads(path.read_text(encoding="utf-8")).get("model")
                            except (OSError, ValueError):
                                result[path.name] = None
                        return result

                    native_before = native_defaults() if client == "claude" else None
                    offset = len(terminal.drain())
                    terminal.send("/model\r")
                    text = terminal.wait(lambda text: ("Synthetic Beta" if client == "codex" else OTHER) in text, start=offset)
                    picker_seen = "Synthetic Beta" in text if client == "codex" else OTHER in text
                    if client == "codex":
                        terminal.send("\x1b[B\r")
                        time.sleep(0.5)
                        terminal.send("\r")
                    else:
                        time.sleep(0.5)
                        terminal.send("\x1b[B")
                        time.sleep(0.3)
                        selected_offset = len(terminal.drain())
                        terminal.send("s")
                        terminal.wait(lambda text: "Setmodelto" + OTHER in re.sub(r"\s", "", text) and "session" in text.lower(), start=selected_offset)
                    time.sleep(0.8)
                    terminal.send("Reply with " + MARKER + ".\r")
                    terminal.wait(
                        lambda _: any(item["task_prompt"] and item["request_kind"] == "conversation" and item["model"] == OTHER for item in observed), 15
                    )
                    terminal.wait(lambda text: MARKER in text)
                    safe_requests = all(
                        item["model"] in {MAIN, OTHER} and (item["request_kind"] != "conversation" or not item["task_prompt"] or item["model"] == OTHER)
                        for item in observed
                    )
                    result = {
                        "passed": picker_seen and safe_requests and config.path.read_bytes() == before,
                        "client": client,
                        "picker_seen": picker_seen,
                        "observed": observed,
                        "defaults_unchanged": config.path.read_bytes() == before,
                        "alias_used": False,
                        "native_defaults_unchanged": native_before == native_defaults() if client == "claude" else None,
                        "cli_version": version.stdout.strip(),
                    }
                    if client == "claude":
                        result["passed"] = result["passed"] and result["native_defaults_unchanged"]
                    report.write_text(json.dumps({**result, "screen": terminal.raw}, indent=2), encoding="utf-8")
                    print(json.dumps(result))
                    return 0 if result["passed"] else 1
        except Exception:
            report.write_text(
                json.dumps({"passed": False, "client": client, "observed": observed, "screen": terminal.raw if terminal else ""}, indent=2), encoding="utf-8"
            )
            raise
        finally:
            if terminal is not None:
                terminal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()
    if args.worker:
        return worker(args.client, args.source, args.report, args.workdir)
    with tempfile.TemporaryDirectory(prefix="protoverta-picker-") as temporary:
        if Path(temporary).resolve().parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError("Fixture directory must stay under the temporary root")
        result = run_process(
            [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:], "--worker", "--workdir", temporary],
            env={**sanitized_environment(), "PYTHONIOENCODING": "utf-8"},
            cwd=Path(__file__).resolve().parents[1],
            timeout=60,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(0.3)  # Windows job termination releases SQLite handles asynchronously.
    print(json.dumps({"stdout": result.stdout[-2500:]}))
    if result.returncode:
        print(json.dumps({"error": result.stderr[-3500:]}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
