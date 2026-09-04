"""Ephemeral per-visitor scoping for the public Playground.

This is demo scoping, not authentication. A session identifier proves only that
whoever presents it started a Playground session on this server; it establishes
no identity, grants no privilege, and must never be treated as a credential.

What it does buy is isolation. Two people using the public deployment at the
same time get separate runs, separate mandates, separate capabilities, separate
replay history and separate onboarded merchants. Without that, one visitor
revoking consent would cancel another visitor's capability, and one visitor's
simulated merchant would appear in another visitor's catalogue. Both would be
confusing; the first would also be a false demonstration of revocation.

Everything here is bounded on purpose: sessions expire, sessions are capped, and
each session may onboard only a small number of merchants. A public demo with an
unbounded per-visitor store is a memory exhaustion primitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from secrets import token_hex
from threading import RLock
from typing import Any

from mandateguard.semantic.evidence import SemanticEvidenceEntry

from mandateguard.sandbox.universe import SandboxProduct


SESSION_ID_RE = re.compile(r"^js_[0-9a-f]{32}$")

#: How long an idle session survives. Long enough for an unhurried walkthrough,
#: short enough that an abandoned tab does not hold memory for an afternoon.
SESSION_TTL = timedelta(hours=2)

#: Ceilings for the public deployment.
MAX_SESSIONS = 512
MAX_ONBOARDED_PER_SESSION = 8
MAX_RUNS_PER_SESSION = 64


class SessionError(RuntimeError):
    """The session is unknown, expired, or over one of its limits."""


@dataclass(slots=True)
class OnboardedMerchant:
    """A simulated merchant a visitor created during their own session."""

    merchant_id: str
    display_name: str
    sku: str
    product: SandboxProduct
    evidence: tuple[SemanticEvidenceEntry, ...]
    source_listing_id: str
    source_listing_title: str
    created_at: str

    def public_mapping(self) -> dict[str, Any]:
        return {
            **self.product.public_mapping(),
            "world": "SANDBOX_ONBOARDED",
            "onboarded": True,
            "source_listing_id": self.source_listing_id,
            "source_listing_title": self.source_listing_title,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class JudgeSession:
    """One visitor's private slice of the Playground."""

    session_id: str
    created_at: datetime
    last_seen_at: datetime
    onboarded: dict[str, OnboardedMerchant] = field(default_factory=dict)
    run_ids: list[str] = field(default_factory=list)

    def public_mapping(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": _iso(self.created_at),
            "onboarded_merchants": len(self.onboarded),
            "runs": len(self.run_ids),
            "limits": {
                "onboarded_merchants": MAX_ONBOARDED_PER_SESSION,
                "runs": MAX_RUNS_PER_SESSION,
                "expires_after_idle_seconds": int(SESSION_TTL.total_seconds()),
            },
            "purpose": "DEMO_SCOPING_NOT_AUTHENTICATION",
        }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class JudgeSessionRegistry:
    """Thread-safe, bounded store of live Playground sessions."""

    __slots__ = ("_sessions", "_lock", "_clock")

    def __init__(self, *, clock: Any = None) -> None:
        self._sessions: dict[str, JudgeSession] = {}
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create(self) -> JudgeSession:
        now = self._clock()
        session = JudgeSession(
            session_id="js_" + token_hex(16), created_at=now, last_seen_at=now
        )
        with self._lock:
            self._expire(now)
            if len(self._sessions) >= MAX_SESSIONS:
                # Evict the least recently seen rather than refuse a visitor.
                oldest = min(self._sessions.values(), key=lambda item: item.last_seen_at)
                del self._sessions[oldest.session_id]
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: object) -> JudgeSession:
        if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
            raise SessionError("session identifier is malformed")
        now = self._clock()
        with self._lock:
            self._expire(now)
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionError("session is unknown or has expired")
            session.last_seen_at = now
            return session

    def resolve_or_create(self, session_id: object) -> tuple[JudgeSession, bool]:
        """Return an existing session, or a fresh one when it cannot be used."""

        if session_id is None:
            return self.create(), True
        try:
            return self.get(session_id), False
        except SessionError:
            return self.create(), True

    def record_run(self, session: JudgeSession, run_id: str) -> None:
        with self._lock:
            if len(session.run_ids) >= MAX_RUNS_PER_SESSION:
                session.run_ids.pop(0)
            session.run_ids.append(run_id)

    def owns_run(self, session: JudgeSession, run_id: str) -> bool:
        with self._lock:
            return run_id in session.run_ids

    def add_onboarded(self, session: JudgeSession, merchant: OnboardedMerchant) -> None:
        with self._lock:
            if len(session.onboarded) >= MAX_ONBOARDED_PER_SESSION:
                raise SessionError(
                    "this session has reached its simulated-onboarding limit"
                )
            session.onboarded[merchant.merchant_id] = merchant

    def onboarded(self, session: JudgeSession) -> tuple[OnboardedMerchant, ...]:
        with self._lock:
            return tuple(
                sorted(session.onboarded.values(), key=lambda item: item.created_at)
            )

    def find_onboarded(
        self, session: JudgeSession, merchant_id: str, sku: str
    ) -> OnboardedMerchant | None:
        with self._lock:
            merchant = session.onboarded.get(merchant_id)
            if merchant is None or merchant.sku != sku:
                return None
            return merchant

    def live_count(self) -> int:
        with self._lock:
            self._expire(self._clock())
            return len(self._sessions)

    def _expire(self, now: datetime) -> None:
        cutoff = now - SESSION_TTL
        for session_id, session in list(self._sessions.items()):
            if session.last_seen_at < cutoff:
                del self._sessions[session_id]
