from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
CLAIMABLE_STATES = frozenset({"queued", "retry_wait", "claimed"})


@dataclass(frozen=True, slots=True)
class QueueJob:
    id: str
    kind: str
    update_id: int | None
    payload: dict[str, Any]
    state: str
    attempt: int
    lease_owner: str | None
    lease_expires_at: float | None
    heartbeat_at: float | None
    retry_at: float | None
    error_code: str | None
    created_at: float
    updated_at: float


class JobQueue:
    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS durable_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    update_id INTEGER UNIQUE,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    heartbeat_at REAL,
                    retry_at REAL,
                    error_code TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_durable_jobs_claim
                    ON durable_jobs(state, retry_at, lease_expires_at, created_at);

                CREATE TABLE IF NOT EXISTS worker_nonces (
                    nonce TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_selections (
                    token TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    value_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_telegram_selections_expiry
                    ON telegram_selections(expires_at);
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> QueueJob | None:
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            payload = {}
        return QueueJob(
            id=str(row["id"]),
            kind=str(row["kind"]),
            update_id=int(row["update_id"]) if row["update_id"] is not None else None,
            payload=payload,
            state=str(row["state"]),
            attempt=int(row["attempt"]),
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
            lease_expires_at=float(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None,
            heartbeat_at=float(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None,
            retry_at=float(row["retry_at"]) if row["retry_at"] is not None else None,
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def accept_update(self, update_id: int, payload: dict[str, Any]) -> tuple[QueueJob, bool]:
        update_id = int(update_id)
        job_id = f"telegram-update:{update_id}"
        now = float(self.clock())
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO durable_jobs (
                    id, kind, update_id, payload_json, state, created_at, updated_at
                ) VALUES (?, 'telegram_update', ?, ?, 'queued', ?, ?)
                """,
                (job_id, update_id, encoded, now, now),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                "SELECT * FROM durable_jobs WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        job = self._decode(row)
        if job is None:
            raise RuntimeError("accepted job could not be read")
        return job, created

    def enqueue(
        self,
        *,
        idempotency_key: str,
        kind: str,
        payload: dict[str, Any],
    ) -> tuple[QueueJob, bool]:
        normalized = str(idempotency_key).strip()
        if not normalized:
            raise ValueError("idempotency key is required")
        job_id = f"{kind}:{normalized}"
        now = float(self.clock())
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO durable_jobs (
                    id, kind, update_id, payload_json, state, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, 'queued', ?, ?)
                """,
                (job_id, str(kind), encoded, now, now),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                "SELECT * FROM durable_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        job = self._decode(row)
        if job is None:
            raise RuntimeError("queued job could not be read")
        return job, created

    def get(self, job_id: str) -> QueueJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM durable_jobs WHERE id = ?",
                (str(job_id),),
            ).fetchone()
        job = self._decode(row)
        if job is None:
            raise KeyError(job_id)
        return job

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        kind: str | None = None,
    ) -> QueueJob | None:
        now = float(self.clock())
        lease_until = now + max(1, int(lease_seconds))
        worker_id = str(worker_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                params: list[Any] = [now, now]
                kind_sql = ""
                if kind is not None:
                    kind_sql = " AND kind = ?"
                    params.append(str(kind))
                row = connection.execute(
                    f"""
                    SELECT * FROM durable_jobs
                    WHERE (
                        state = 'queued'
                        OR (state = 'retry_wait' AND COALESCE(retry_at, 0) <= ?)
                        OR (state = 'claimed' AND COALESCE(lease_expires_at, 0) <= ?)
                    )
                    {kind_sql}
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    tuple(params),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                connection.execute(
                    """
                    UPDATE durable_jobs
                    SET state = 'claimed',
                        attempt = attempt + 1,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        heartbeat_at = ?,
                        error_code = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (worker_id, lease_until, now, now, str(row["id"])),
                )
                claimed = connection.execute(
                    "SELECT * FROM durable_jobs WHERE id = ?",
                    (str(row["id"]),),
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self._decode(claimed)

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int,
    ) -> bool:
        now = float(self.clock())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE durable_jobs
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND state = 'claimed' AND lease_owner = ?
                """,
                (
                    now,
                    now + max(1, int(lease_seconds)),
                    now,
                    str(job_id),
                    str(worker_id),
                ),
            )
        return cursor.rowcount == 1

    def complete(self, job_id: str, *, worker_id: str | None = None) -> bool:
        return self._terminal_transition(
            job_id,
            state="completed",
            error_code=None,
            worker_id=worker_id,
        )

    def cancel(self, job_id: str, *, worker_id: str | None = None) -> bool:
        return self._terminal_transition(
            job_id,
            state="cancelled",
            error_code=None,
            worker_id=worker_id,
        )

    def _terminal_transition(
        self,
        job_id: str,
        *,
        state: str,
        error_code: str | None,
        worker_id: str | None,
    ) -> bool:
        now = float(self.clock())
        clauses = ["id = ?", "state NOT IN ('completed', 'failed', 'cancelled')"]
        params: list[Any] = [state, error_code, now, str(job_id)]
        if worker_id is not None:
            clauses.append("lease_owner = ?")
            params.append(str(worker_id))
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE durable_jobs
                SET state = ?, error_code = ?, lease_owner = NULL,
                    lease_expires_at = NULL, retry_at = NULL, updated_at = ?
                WHERE {' AND '.join(clauses)}
                """,
                tuple(params),
            )
        return cursor.rowcount == 1

    def fail(
        self,
        job_id: str,
        error_code: str,
        *,
        retry_at: float | None = None,
        worker_id: str | None = None,
    ) -> bool:
        now = float(self.clock())
        target = "retry_wait" if retry_at is not None else "failed"
        clauses = ["id = ?", "state NOT IN ('completed', 'failed', 'cancelled')"]
        params: list[Any] = [target, str(error_code)[:120], retry_at, now, str(job_id)]
        if worker_id is not None:
            clauses.append("lease_owner = ?")
            params.append(str(worker_id))
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE durable_jobs
                SET state = ?, error_code = ?, retry_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE {' AND '.join(clauses)}
                """,
                tuple(params),
            )
        return cursor.rowcount == 1

    def queue_depth(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM durable_jobs
                WHERE state IN ('queued', 'claimed', 'retry_wait')
                """
            ).fetchone()
        return int(row["count"] if row else 0)

    def active_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM durable_jobs WHERE state = 'claimed'"
            ).fetchone()
        return int(row["count"] if row else 0)

    def consume_nonce(self, nonce: str, *, expires_at: float) -> bool:
        nonce = str(nonce).strip()
        if not nonce:
            return False
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("DELETE FROM worker_nonces WHERE expires_at <= ?", (now,))
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO worker_nonces(nonce, expires_at, created_at)
                VALUES (?, ?, ?)
                """,
                (nonce, float(expires_at), now),
            )
        return cursor.rowcount == 1


class DurableSelectionStore:
    def __init__(
        self,
        database_path: str | Path,
        ttl_seconds: int = 15 * 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.queue = JobQueue(database_path, clock=clock)
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.clock = clock

    def create(self, owner_id: int, value: dict[str, Any]) -> str:
        self.cleanup()
        token = secrets.token_urlsafe(8)
        now = float(self.clock())
        with self.queue._connect() as connection:
            connection.execute(
                """
                INSERT INTO telegram_selections(
                    token, owner_id, value_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    token,
                    int(owner_id),
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    now + self.ttl_seconds,
                    now,
                ),
            )
        return token

    def get(self, token: str, owner_id: int) -> dict[str, Any] | None:
        now = float(self.clock())
        with self.queue._connect() as connection:
            row = connection.execute(
                """
                SELECT value_json, expires_at FROM telegram_selections
                WHERE token = ? AND owner_id = ?
                """,
                (str(token), int(owner_id)),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now:
                connection.execute(
                    "DELETE FROM telegram_selections WHERE token = ?",
                    (str(token),),
                )
                return None
        value = json.loads(str(row["value_json"]))
        return value if isinstance(value, dict) else None

    def pop(self, token: str, owner_id: int) -> dict[str, Any] | None:
        value = self.get(token, owner_id)
        if value is None:
            return None
        with self.queue._connect() as connection:
            connection.execute(
                "DELETE FROM telegram_selections WHERE token = ? AND owner_id = ?",
                (str(token), int(owner_id)),
            )
        return value

    def cleanup(self) -> int:
        with self.queue._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM telegram_selections WHERE expires_at <= ?",
                (float(self.clock()),),
            )
        return cursor.rowcount

    @property
    def count(self) -> int:
        self.cleanup()
        with self.queue._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM telegram_selections"
            ).fetchone()
        return int(row["count"] if row else 0)
