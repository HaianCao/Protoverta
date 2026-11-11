"""Opt-in per-model local token reservations shared by independent sessions.

No provider billing guarantee: missing usage and ambiguous failures retain the
full reservation. No prompts, raw model IDs or credentials enter the database.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Mapping
from os import PathLike
from typing import TYPE_CHECKING, Any

from .admission import AdmissionDenied, AdmissionPolicyError, SharedAdmission
from .context import TokenCount
from .errors import ConversionError

if TYPE_CHECKING:
    from .server import GatewayConfig

_SQLITE_MAX = 2**63 - 1


def output_reserve(body: Mapping[str, Any], protocol: str) -> int:
    fields = (
        ("max_tokens", "max_completion_tokens")
        if protocol == "chat"
        else ("max_output_tokens",)
        if protocol == "responses"
        else ("max_tokens",)
    )
    values = [body[key] for key in fields if key in body]
    if len(values) != 1 or type(values[0]) is not int or values[0] <= 0:
        raise ConversionError("token quota requires one explicit positive output cap")
    return values[0]


def input_reserve(config: GatewayConfig, body: dict[str, Any]) -> TokenCount:
    from .context import assess_context

    assessment = assess_context(body, config.upstream_protocol, None, policy="estimate", counter=config.token_counter)
    if not assessment.exact or assessment.input_tokens is None:
        raise ConversionError("token quota requires an exact operator input count")
    return TokenCount(assessment.input_tokens, exact=True)


class SharedTokenQuota:
    def __init__(
        self,
        path: str | PathLike[str],
        scope: str,
        model: str,
        *,
        limit: int,
        window: float = 60,
        lease: float = 610,
        clock: Callable[[], float] | None = None,
    ) -> None:
        options: dict[str, Any] = {"window": window, "lease": lease}
        if clock is not None:
            options["clock"] = clock
        self.storage = SharedAdmission(path, scope + "\0token-model\0" + model, **options)
        self.limit = limit
        if type(limit) is not int or not 0 < limit <= _SQLITE_MAX:
            raise ValueError("token quota limit must be a positive signed-64-bit integer")

    def reserve(self, amount: int) -> str:
        if type(amount) is not int or amount < 0:
            raise ValueError("token reservation must be nonnegative")
        storage = self.storage
        now, identifier = storage.clock(), uuid.uuid4().hex
        with storage._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS token_policies (scope TEXT PRIMARY KEY, quota INTEGER, window REAL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS token_reservations (id TEXT PRIMARY KEY, scope TEXT, started REAL, active_until REAL, amount INTEGER)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS token_reservations_scope ON token_reservations(scope)")
            db.execute(
                "INSERT OR IGNORE INTO token_policies VALUES (?,?,?)", (storage.scope, self.limit, storage.window)
            )
            if db.execute("SELECT quota,window FROM token_policies WHERE scope=?", (storage.scope,)).fetchone() != (
                self.limit,
                storage.window,
            ):
                raise AdmissionPolicyError("Conflicting per-model token quota policy.")
            db.execute(
                "DELETE FROM token_reservations WHERE scope=? AND started<=? AND active_until<=?",
                (storage.scope, now - storage.window, now),
            )
            entries = db.execute(
                "SELECT started,active_until,amount FROM token_reservations WHERE scope=?", (storage.scope,)
            ).fetchall()
            if amount > self.limit or sum(row[2] for row in entries) + amount > self.limit:
                wait = min((max(row[0] + storage.window, row[1]) - now for row in entries), default=storage.window)
                raise AdmissionDenied(max(1, math.ceil(wait)))
            db.execute(
                "INSERT INTO token_reservations VALUES (?,?,?,?,?)",
                (identifier, storage.scope, now, now + storage.lease, amount),
            )
        return identifier

    def reconcile(self, identifier: str, inputs: int | None = None, outputs: int | None = None) -> None:
        # Unknown/partial usage must never be interpreted as zero or refunded.
        storage = self.storage
        with storage._transaction() as db:
            if type(inputs) is int and type(outputs) is int and inputs >= 0 and outputs >= 0:
                # Saturation is internal admission accounting only. It still
                # exhausts every permitted quota; never changes response usage.
                db.execute(
                    "UPDATE token_reservations SET started=?,active_until=0,amount=? WHERE id=? AND scope=?",
                    (storage.clock(), min(_SQLITE_MAX, inputs + outputs), identifier, storage.scope),
                )
            else:
                db.execute(
                    "UPDATE token_reservations SET started=?,active_until=0 WHERE id=? AND scope=?",
                    (storage.clock(), identifier, storage.scope),
                )
