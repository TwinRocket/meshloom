"""Consecutive-failure circuit breaker shared by the fixed-host clients.

Used by the Community client (``services/meshloom_community.py``) and the share
service client (``services/share.py``), each with its own instance and
thresholds. Closed until ``threshold`` consecutive failures, then open for
``open_seconds`` (no call at all), then half-open: exactly one trial call goes
out and its outcome closes the breaker or opens it again. In memory only; a
restart closes it.

The caller decides what a failure is (a transport error, a timeout, a 5xx...)
and must release a granted half-open trial that never reached a verdict
(``release_trial()``, on a cancellation or a local bug).
"""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    """Closed / open / half-open breaker on consecutive failures."""

    def __init__(
        self,
        *,
        threshold: int,
        open_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self.clock = clock
        self.failures = 0
        self.open_until = 0.0
        self.trial_in_flight = False

    def _now(self, now: float | None) -> float:
        return self.clock() if now is None else now

    def allow(self, *, now: float | None = None) -> bool:
        """True when a call may go out. Half-open lets exactly one trial through."""
        if self.failures < self.threshold:
            return True
        if self._now(now) < self.open_until or self.trial_in_flight:
            return False
        self.trial_in_flight = True
        return True

    def cooling(self, *, now: float | None = None) -> bool:
        """True while open and before ``open_until`` (a check that grants no trial)."""
        return self.is_open and self._now(now) < self.open_until

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.trial_in_flight = False

    def record_failure(self, *, now: float | None = None) -> None:
        self.failures += 1
        self.trial_in_flight = False
        if self.failures >= self.threshold:
            self.open_until = self._now(now) + self.open_seconds

    def release_trial(self) -> None:
        """Give back a half-open trial without judging the service."""
        self.trial_in_flight = False

    @property
    def is_open(self) -> bool:
        return self.failures >= self.threshold
