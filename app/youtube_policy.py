from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, MutableMapping


RATE_LIMITED = "youtube_rate_limited"
BOT_CHECK = "youtube_bot_check"
LOGIN_REQUIRED = "youtube_login_required"
FRAGMENT_FORBIDDEN = "youtube_fragment_forbidden"
UNSUPPORTED_CLIENT = "youtube_unsupported_client"
TRANSIENT = "youtube_transient"

_KNOWN_FAILURES = frozenset(
    {
        RATE_LIMITED,
        BOT_CHECK,
        LOGIN_REQUIRED,
        FRAGMENT_FORBIDDEN,
        UNSUPPORTED_CLIENT,
        TRANSIENT,
        "youtube_circuit_open",
        "youtube_no_media_file",
        "youtube_no_metadata",
        "youtube_output_without_audio",
        "youtube_output_without_video",
    }
)
_BLOCKING_FAILURES = frozenset({RATE_LIMITED, BOT_CHECK, LOGIN_REQUIRED})


def classify_youtube_error(exc: BaseException | str) -> str:
    text = str(exc).strip().lower().replace("’", "'")
    if text in _KNOWN_FAILURES:
        return text
    if "googlevideo" in text and any(value in text for value in ("403", "forbidden")):
        return FRAGMENT_FORBIDDEN
    if "429" in text or "too many requests" in text:
        return RATE_LIMITED
    if "confirm you're not a bot" in text or "confirm you are not a bot" in text:
        return BOT_CHECK
    if any(
        value in text
        for value in (
            "login_required",
            "login required",
            "log in to continue",
            "sign in to continue",
            "authentication required",
        )
    ):
        return LOGIN_REQUIRED
    if "unsupported client" in text or "skipping unsupported client" in text:
        return UNSUPPORTED_CLIENT
    return TRANSIENT


def youtube_retry_allowed(classification: str, attempt: int) -> bool:
    attempt_number = int(attempt)
    if classification == FRAGMENT_FORBIDDEN:
        return attempt_number == 0
    if classification == TRANSIENT:
        return attempt_number < 2
    return False


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    state: str
    failure_count: int
    opened_until: float
    last_failure: str


class YouTubeCircuit:
    """Small storage-agnostic circuit breaker for YouTube egress blocks."""

    def __init__(
        self,
        *,
        store: MutableMapping[str, object],
        cooldown_seconds: int = 900,
        max_cooldown_seconds: int = 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.max_cooldown_seconds = max(self.cooldown_seconds, int(max_cooldown_seconds))
        self.clock = clock
        self.store.setdefault("state", "closed")
        self.store.setdefault("failure_count", 0)
        self.store.setdefault("opened_until", 0.0)
        self.store.setdefault("last_failure", "")
        self.store.setdefault("canary_in_flight", False)

    def allow_request(self) -> bool:
        state = str(self.store.get("state") or "closed")
        if state == "closed":
            return True

        now = float(self.clock())
        opened_until = float(self.store.get("opened_until") or 0.0)
        if state == "open" and now < opened_until:
            return False
        if state == "open":
            self.store["state"] = "half_open"
            self.store["canary_in_flight"] = False

        if bool(self.store.get("canary_in_flight")):
            return False
        self.store["canary_in_flight"] = True
        return True

    def record_failure(self, classification: str) -> None:
        self.store["last_failure"] = str(classification)
        if classification not in _BLOCKING_FAILURES:
            self.store["canary_in_flight"] = False
            return

        failures = int(self.store.get("failure_count") or 0) + 1
        exponent = min(6, max(0, failures - 1))
        cooldown = min(self.max_cooldown_seconds, self.cooldown_seconds * (2**exponent))
        self.store["failure_count"] = failures
        self.store["state"] = "open"
        self.store["opened_until"] = float(self.clock()) + cooldown
        self.store["canary_in_flight"] = False

    def record_success(self) -> None:
        self.store.update(
            state="closed",
            failure_count=0,
            opened_until=0.0,
            last_failure="",
            canary_in_flight=False,
        )

    def snapshot(self) -> CircuitSnapshot:
        return CircuitSnapshot(
            state=str(self.store.get("state") or "closed"),
            failure_count=int(self.store.get("failure_count") or 0),
            opened_until=float(self.store.get("opened_until") or 0.0),
            last_failure=str(self.store.get("last_failure") or ""),
        )
