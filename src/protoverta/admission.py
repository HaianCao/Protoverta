"""Opt-in cross-process request budgets. No prompts or credentials are stored."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
import time
import uuid
import weakref
from _thread import LockType
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from os import PathLike
from pathlib import Path

_LOCAL_LOCKS: weakref.WeakValueDictionary[Path, LockType] = weakref.WeakValueDictionary()
_LOCAL_LOCKS_GUARD = threading.Lock()


class AdmissionDenied(Exception):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__("Shared provider request budget reached.")


class AdmissionPolicyError(ValueError):
    pass


# How long a transaction waits for the database lock (SQLite busy timeout and
# the local writer lock). One connection is kept per SharedAdmission in WAL
# mode; on Windows, file scanners can still briefly hold the database or its
# log, and a 1 s wait rejected healthy requests on CI runners. Contention beyond
# this bound fails closed with AdmissionStorageError.
STORAGE_WAIT_SECONDS = 5.0


class AdmissionStorageError(RuntimeError):
    """Local budget storage failed; never classify this as upstream transport."""


class SharedAdmission:
    def __init__(
        self,
        path: str | PathLike[str],
        scope: str,
        *,
        requests: int = 40,
        window: float = 60.0,
        concurrency: int = 5,
        lease: float = 610.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if type(requests) is not int or requests < 1 or type(concurrency) is not int or concurrency < 1:
            raise ValueError("shared request and concurrency limits must be positive integers")
        if any(isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0 for value in (window, lease)):
            raise ValueError("shared time limits must be finite positive numbers")
        self.path = Path(path).expanduser().resolve()
        with _LOCAL_LOCKS_GUARD:
            lock = _LOCAL_LOCKS.get(self.path)
            if lock is None:
                lock = threading.Lock()
                _LOCAL_LOCKS[self.path] = lock
            self._local_lock = lock
        self.scope = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        self.requests, self.window, self.concurrency, self.lease = requests, window, concurrency, lease
        self.clock = clock
        self._connection: sqlite3.Connection | None = None
        self._closed = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=STORAGE_WAIT_SECONDS, isolation_level=None, check_same_thread=False)
        try:
            # WAL keeps one log file instead of creating and deleting a rollback
            # journal on every write; on Windows that create/delete is what file
            # scanners stall on. The mode is stored in the database file, and
            # older processes on the same file follow it. If the filesystem or
            # a concurrent connection refuses WAL, keep the current mode and its
            # full sync; NORMAL is only corruption-safe under WAL.
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode is not None and str(mode[0]).lower() == "wal":
                connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("CREATE TABLE IF NOT EXISTS budgets (scope TEXT PRIMARY KEY, requests INTEGER, window REAL, concurrency INTEGER, cooldown REAL)")
            connection.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, scope TEXT, started REAL, active_until REAL)")
            connection.execute("CREATE INDEX IF NOT EXISTS attempts_scope ON attempts(scope)")
            connection.execute("CREATE INDEX IF NOT EXISTS attempts_rate ON attempts(scope,started)")
            connection.execute("CREATE INDEX IF NOT EXISTS attempts_active ON attempts(scope,active_until)")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        # Serialize local writers BEFORE SQLite's busy wait. Several handler
        # threads spinning on independent connections can starve each other.
        # Other processes remain coordinated by SQLite; contention fails closed.
        if not self._local_lock.acquire(timeout=STORAGE_WAIT_SECONDS):
            raise AdmissionStorageError("Shared provider budget storage is unavailable.")
        try:
            with self._database_transaction() as connection:
                yield connection
        finally:
            self._local_lock.release()

    def _open(self) -> sqlite3.Connection:
        """Storage access for one transaction: the kept connection or a new one."""
        connection, self._connection = self._connection, None
        return connection if connection is not None else self._connect()

    @contextmanager
    def _database_transaction(self) -> Iterator[sqlite3.Connection]:
        # One connection is reused across transactions (always under the local
        # lock). Opening and closing per transaction cost more than the write
        # itself and, in WAL mode, the last close also checkpoints and deletes
        # the log. A storage failure discards the connection; a policy outcome
        # (for example AdmissionDenied) rolls back and keeps it.
        connection = None
        try:
            try:
                connection = self._open()
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except (OSError, sqlite3.Error):
                    raise
                except BaseException:
                    connection.execute("ROLLBACK")
                    self._keep(connection)
                    connection = None
                    raise
                connection.execute("COMMIT")
                self._keep(connection)
                connection = None
            finally:
                if connection is not None:
                    connection.close()
        except (OSError, sqlite3.Error) as exc:
            # Do not expose database paths or driver messages to HTTP clients.
            raise AdmissionStorageError("Shared provider budget storage is unavailable.") from exc

    def _keep(self, connection: sqlite3.Connection) -> None:
        if self._closed:
            connection.close()
        else:
            self._connection = connection

    def close(self) -> None:
        """Close the kept connection. Later transactions still work but no
        longer keep a connection open, so a straggling request cannot leak one."""
        with self._local_lock:
            self._closed = True
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def acquire(self) -> str:
        now = self.clock()
        identifier = uuid.uuid4().hex
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO budgets VALUES (?,?,?,?,0)", (self.scope, self.requests, self.window, self.concurrency))
            policy = db.execute("SELECT requests,window,concurrency,cooldown FROM budgets WHERE scope=?", (self.scope,)).fetchone()
            if policy is None:
                raise AdmissionStorageError("Shared provider budget record is unavailable.")
            if policy[:3] != (self.requests, self.window, self.concurrency):
                raise AdmissionPolicyError("Conflicting shared budget policy for the same upstream account.")
            db.execute("DELETE FROM attempts WHERE scope=? AND started<=? AND active_until<=?", (self.scope, now - self.window, now))
            rate = db.execute("SELECT COUNT(*),MIN(started) FROM attempts WHERE scope=? AND started>?", (self.scope, now - self.window)).fetchone()
            active = db.execute("SELECT COUNT(*),MIN(active_until) FROM attempts WHERE scope=? AND active_until>?", (self.scope, now)).fetchone()
            wait = max(0, policy[3] - now)
            if rate[0] >= self.requests:
                wait = max(wait, rate[1] + self.window - now)
            if active[0] >= self.concurrency:
                wait = max(wait, active[1] - now)
            if wait > 0:
                raise AdmissionDenied(wait)
            db.execute("INSERT INTO attempts VALUES (?,?,?,?)", (identifier, self.scope, now, now + self.lease))
        return identifier

    def release(self, identifier: str | None) -> None:
        with self._transaction() as db:
            db.execute("UPDATE attempts SET active_until=0 WHERE id=? AND scope=?", (identifier, self.scope))

    def cooldown(self, seconds: float) -> None:
        if not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("cooldown must be finite and nonnegative")
        with self._transaction() as db:
            db.execute("UPDATE budgets SET cooldown=MAX(cooldown,?) WHERE scope=?", (self.clock() + seconds, self.scope))
