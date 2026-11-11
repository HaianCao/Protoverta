"""Bounded opt-in session journals containing fixed observations only."""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from os import PathLike
from pathlib import Path
from typing import Any, TextIO

from .observability import RequestObservation

_NAME = re.compile(r"session-[a-f0-9]{32}\.jsonl\Z")
_FIELDS = set(RequestObservation.__dataclass_fields__)
_OPTIONAL = {"first_payload_ms", "input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens"}
_DIGESTS = {"requested_model_digest", "effective_model_digest", "agent_digest", "chain_digest"}
_TRACE_META = {"phase", "feature"}
_PHASES = {"request", "preflight", "admission", "upstream", "response", "stream"}
_FEATURES = {
    "refusal",
    "content_filter",
    "stop_sequence",
    "pause_turn",
    "stream_usage_policy",
    "codex_closed_resume",
    "compaction",
    "reasoning",
    "reasoning_omitted",
}


def _journals(root: Path) -> tuple[list[Path], bool]:
    # Inspect at most 512 directory entries, including unrelated filenames.
    # A crowded directory yields a bounded sample, never a global newest claim.
    paths: list[tuple[float, Path]] = []
    limited = False
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 512:
                limited = True
                break
            try:
                if _NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    paths.append((entry.stat(follow_symlinks=False).st_mtime, Path(entry.path)))
            except OSError:
                continue
    return [path for _, path in sorted(paths, key=lambda item: item[0], reverse=True)], limited


class _JournalSink:
    """Nonblocking producer; the daemon writer exclusively owns its stream."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.pending: queue.Queue[str] = queue.Queue(maxsize=128)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.accepted = self.dropped = self.byte_count = 0
        self.thread = threading.Thread(target=self._write, name="protoverta-journal", daemon=True)
        self.thread.start()

    def __call__(self, record: object) -> None:
        if type(record) is not RequestObservation:
            return
        line = json.dumps(asdict(record), separators=(",", ":")) + "\n"
        size = len(line.encode())
        with self.lock:
            if (
                self.stopping.is_set()
                or self.accepted >= 1024
                or size > 4096
                or self.byte_count + size > 1024 * 1024 - 512
            ):
                self.dropped += 1
                return
            try:
                self.pending.put_nowait(line)
            except queue.Full:
                self.dropped += 1
                return
            self.accepted += 1
            self.byte_count += size

    def _write(self) -> None:
        try:
            while not self.stopping.is_set() or not self.pending.empty():
                try:
                    line = self.pending.get(timeout=0.05)
                except queue.Empty:
                    continue
                self.stream.write(line)
                self.stream.flush()
            with self.lock:
                dropped = self.dropped
            self.stream.write(json.dumps({"kind": "protoverta-summary", "dropped_records": dropped}) + "\n")
            self.stream.flush()
        except (OSError, ValueError):
            pass  # Optional diagnostics cannot change request success/failure.
        finally:
            self.stream.close()

    def close(self) -> None:
        with self.lock:
            self.stopping.set()
        # Never close a stream from under a blocked writer. It remains daemon-
        # owned and closes when the filesystem call returns; no durable guarantee.
        self.thread.join(timeout=0.25)


@contextmanager
def diagnostic_journal(directory: str | PathLike[str]) -> Iterator[_JournalSink]:
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Only our fixed-name, non-symlink journals with the ownership header qualify.
    journals, _ = _journals(root)
    for path in journals[31:]:
        try:
            with path.open("r", encoding="utf-8") as source:
                header = json.loads(source.readline(512))
            if (
                isinstance(header, dict)
                and header.get("kind") == "protoverta-session"
                and header.get("schema_version") in (1, 2)
            ):
                path.unlink()
        except (OSError, ValueError):
            pass
    path = root / ("session-" + secrets.token_hex(16) + ".jsonl")
    stream = path.open("x", encoding="utf-8")
    try:
        stream.write(json.dumps({"kind": "protoverta-session", "schema_version": 2}) + "\n")
        stream.flush()
        observe = _JournalSink(stream)
    except BaseException:
        stream.close()
        raise
    try:
        yield observe
    finally:
        observe.close()


def latest_diagnostics(directory: str | PathLike[str]) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return {"records": [], "limited": False}
    journals, scan_limited = _journals(root)
    for path in journals:
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            with path.open("r", encoding="utf-8") as stream:
                header = json.loads(stream.readline(512))
                if (
                    not isinstance(header, dict)
                    or set(header) != {"kind", "schema_version"}
                    or header.get("kind") != "protoverta-session"
                    or header.get("schema_version") not in (1, 2)
                ):
                    continue
                schema_version = header["schema_version"]
                records: list[dict[str, Any]] = []
                dropped = 0
                for index, line in enumerate(stream):
                    if index >= 1025 or len(line) > 4096:
                        break
                    value = json.loads(line)
                    if (
                        isinstance(value, dict)
                        and set(value) == {"kind", "dropped_records"}
                        and value["kind"] == "protoverta-summary"
                        and type(value["dropped_records"]) is int
                        and value["dropped_records"] >= 0
                    ):
                        dropped = value["dropped_records"]
                        continue
                    if (
                        not isinstance(value, dict)
                        or set(value) - _FIELDS
                        or not (_FIELDS - _OPTIONAL - _DIGESTS - _TRACE_META) <= set(value)
                    ):
                        continue
                    for key in _OPTIONAL:
                        value.setdefault(key, None)
                    for key in _DIGESTS:
                        value.setdefault(key, None)
                    for key in _TRACE_META:
                        value.setdefault(key, None)
                    if any(
                        value[key] is not None
                        and (not isinstance(value[key], str) or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None)
                        for key in _DIGESTS
                    ):
                        continue
                    if value["phase"] is not None and value["phase"] not in _PHASES:
                        continue
                    if value["feature"] is not None and value["feature"] not in _FEATURES:
                        continue
                    if not isinstance(value["request_id"], str) or not re.fullmatch(
                        r"[a-f0-9]{32}", value["request_id"]
                    ):
                        continue
                    if value["protocol"] not in ("chat", "messages", "responses", "models", "unknown") or value[
                        "upstream_protocol"
                    ] not in (
                        "chat",
                        "messages",
                        "responses",
                        "unknown",
                    ):
                        continue
                    from typing import get_args

                    from .observability import Outcome

                    if (
                        value["outcome"] not in get_args(Outcome)
                        or type(value["duration_ms"]) is not int
                        or not 0 <= value["duration_ms"] <= 86400000
                        or type(value["streaming"]) is not bool
                    ):
                        continue
                    if value["status"] is not None and (
                        type(value["status"]) is not int or not 100 <= value["status"] <= 599
                    ):
                        continue
                    if any(
                        value[key] is not None and (type(value[key]) is not int or value[key] < 0) for key in _OPTIONAL
                    ):
                        continue
                    records.append(value)
            return {
                "records": records[-100:],
                "limited": scan_limited or len(records) >= 100 or dropped > 0,
                "dropped_records": dropped,
                "schema_version": schema_version,
            }
        except (OSError, ValueError, TypeError):
            continue
    return {"records": [], "limited": scan_limited}
