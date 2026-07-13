"""Guardrails for the public demo backend: rate limiting, allowlisting, budget caps.

Kept deliberately simple (in-memory / file-based) for the local-demo pass.
Every piece here is designed to be swappable for Redis / Modal Dict later
without touching callers — see the TODOs.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

# --- Scenario allowlist -----------------------------------------------------

ALLOWED_SCENARIO_IDS: frozenset[str] = frozenset(
    {
        "checkout-deploy",
        "slack-vague",
        "latency-no-deploy",
        "past-incident-match",
    }
)


def is_valid_scenario_id(scenario_id: str) -> bool:
    return scenario_id in ALLOWED_SCENARIO_IDS


# --- Request body size cap --------------------------------------------------

MAX_BODY_BYTES = 1024  # 1KB — request only needs {"scenario_id": "..."}


# --- Rate limiting (in-memory, per-process) ---------------------------------
#
# TODO(prod): swap the in-memory deques for Redis (or Modal Dict) so limits
# are shared across worker processes / cold starts. The interface
# (RateLimiter.check) stays the same either way.


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0
    reason: str = ""


class RateLimiter:
    """Per-IP sliding-window rate limiter: 5 req/10min, 20 req/24h."""

    SHORT_WINDOW_SECONDS = 10 * 60
    SHORT_WINDOW_MAX = 5
    LONG_WINDOW_SECONDS = 24 * 60 * 60
    LONG_WINDOW_MAX = 20

    def __init__(self, exempt_ips: frozenset[str] | None = None) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._exempt_ips = exempt_ips or frozenset()

    def check(self, ip: str) -> RateLimitResult:
        if ip in self._exempt_ips:
            return RateLimitResult(allowed=True)

        now = time.monotonic()
        with self._lock:
            history = self._requests[ip]

            # Drop entries older than the long window.
            while history and now - history[0] > self.LONG_WINDOW_SECONDS:
                history.popleft()

            short_count = sum(1 for t in history if now - t <= self.SHORT_WINDOW_SECONDS)
            long_count = len(history)

            if short_count >= self.SHORT_WINDOW_MAX:
                oldest_in_window = next(
                    t for t in history if now - t <= self.SHORT_WINDOW_SECONDS
                )
                retry_after = int(self.SHORT_WINDOW_SECONDS - (now - oldest_in_window)) + 1
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=max(retry_after, 1),
                    reason="Maximum 5 requests per 10 minutes from your IP",
                )

            if long_count >= self.LONG_WINDOW_MAX:
                retry_after = int(self.LONG_WINDOW_SECONDS - (now - history[0])) + 1
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=max(retry_after, 1),
                    reason="Maximum 20 requests per 24 hours from your IP",
                )

            history.append(now)
            return RateLimitResult(allowed=True)


def _exempt_ips_from_env() -> frozenset[str]:
    raw = os.environ.get("RATE_LIMIT_EXEMPT_IPS", "")
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


rate_limiter = RateLimiter(exempt_ips=_exempt_ips_from_env())


# --- Budget tracking (file-based, monthly reset) -----------------------------
#
# TODO(prod): move to Modal Dict for cross-instance consistency.

BUDGET_MONTHLY_CAP_USD = 50.0
BUDGET_WARN_THRESHOLD_PCT = 80.0

# Sonnet 4 pricing (per PRD 3.5.2)
_PROMPT_COST_PER_1K = 0.003
_COMPLETION_COST_PER_1K = 0.015

_BUDGET_FILE = Path(os.environ.get("SENTINEL_BUDGET_FILE", "backend/.cache/budget.json"))


class BudgetTracker:
    """Tracks cumulative Anthropic API cost for the current calendar month."""

    def __init__(self, path: Path = _BUDGET_FILE) -> None:
        self._path = path
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self._path.exists():
            return {"month": self._current_month(), "cost_usd": 0.0}
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"month": self._current_month(), "cost_usd": 0.0}
        if data.get("month") != self._current_month():
            return {"month": self._current_month(), "cost_usd": 0.0}
        return data

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data))

    @staticmethod
    def _current_month() -> str:
        return time.strftime("%Y-%m")

    def record_call(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Record an LLM call's cost, return cumulative month-to-date cost."""
        cost = (
            prompt_tokens * _PROMPT_COST_PER_1K + completion_tokens * _COMPLETION_COST_PER_1K
        ) / 1000.0
        with self._lock:
            data = self._load()
            data["cost_usd"] = data.get("cost_usd", 0.0) + cost
            self._save(data)
            return data["cost_usd"]

    def used_pct(self) -> float:
        with self._lock:
            data = self._load()
        return round(100.0 * data.get("cost_usd", 0.0) / BUDGET_MONTHLY_CAP_USD, 1)

    def is_exhausted(self) -> bool:
        if os.environ.get("FORCE_ENABLE", "").lower() == "true":
            return False
        return self.used_pct() >= 100.0

    def is_warning(self) -> bool:
        return self.used_pct() >= BUDGET_WARN_THRESHOLD_PCT


budget_tracker = BudgetTracker()
