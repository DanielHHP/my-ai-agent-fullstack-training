"""Harness-owned heartbeat for one Run lease."""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable

from .domain import ErrorCode, RunLease
from .state_store import StateStoreError
from .storage import SQLiteStorage, StorageError

_LOGGER = logging.getLogger(__name__)
_HEARTBEAT_ACTOR = "harness-heartbeat"


class LeaseHeartbeat:
    """Renew one Run lease independently from blocking Agent execution."""

    def __init__(
        self,
        *,
        storage: SQLiteStorage,
        lease: RunLease,
        ttl_seconds: int,
        interval_seconds: float,
        retry_delay_seconds: float = 0.25,
        on_renew: Callable[[RunLease], None] | None = None,
        on_failure: Callable[[ErrorCode, str], None] | None = None,
    ) -> None:
        if not isinstance(ttl_seconds, int) or not 10 <= ttl_seconds <= 300:
            raise ValueError("heartbeat TTL must be between 10 and 300 seconds")
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or interval_seconds <= 0
            or interval_seconds >= ttl_seconds
        ):
            raise ValueError("heartbeat interval must be positive and shorter than TTL")
        if (
            isinstance(retry_delay_seconds, bool)
            or not isinstance(retry_delay_seconds, (int, float))
            or not math.isfinite(retry_delay_seconds)
            or retry_delay_seconds < 0
        ):
            raise ValueError("heartbeat retry delay must be finite and non-negative")

        self._storage = storage
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = float(interval_seconds)
        self._retry_delay_seconds = float(retry_delay_seconds)
        self._on_renew = on_renew
        self._on_failure = on_failure
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease_lock = threading.Lock()
        self._lease = lease
        self._failure_lock = threading.Lock()
        self._failure_code: ErrorCode | None = None
        self._failure_message: str | None = None

    @property
    def lease(self) -> RunLease:
        """Return the most recently renewed lease."""

        with self._lease_lock:
            return self._lease

    @property
    def failure_code(self) -> ErrorCode | None:
        """Return the terminal heartbeat failure, if any."""

        with self._failure_lock:
            return self._failure_code

    @property
    def failure_message(self) -> str | None:
        """Return a bounded, non-secret heartbeat failure description."""

        with self._failure_lock:
            return self._failure_message

    @property
    def is_alive(self) -> bool:
        """Return whether the heartbeat thread is currently running."""

        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the single heartbeat thread."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-heartbeat-{self.lease.run_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop and join the heartbeat before the caller releases its lease."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                raise RuntimeError("lease heartbeat did not stop before lease release")

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            if self._renew_once():
                continue
            if self._stop_event.wait(self._retry_delay_seconds):
                return
            if self._renew_once():
                continue
            self._record_failure(
                ErrorCode.STATE_LEASE_HEARTBEAT_FAILED,
                "lease heartbeat failed twice consecutively",
            )
            return

    def _renew_once(self) -> bool:
        try:
            with self._storage.transaction(actor=_HEARTBEAT_ACTOR) as unit:
                renewed = unit.state.renew_lease(self.lease, ttl_seconds=self._ttl_seconds)
        except (StateStoreError, StorageError) as exc:
            _LOGGER.warning("lease heartbeat renewal failed: %s", exc.code.value)
            return False
        except Exception:
            _LOGGER.exception("lease heartbeat renewal failed unexpectedly")
            return False

        with self._lease_lock:
            self._lease = renewed
        if self._on_renew is not None:
            try:
                self._on_renew(renewed)
            except Exception:
                _LOGGER.exception("lease heartbeat renewal callback failed")
        return True

    def _record_failure(self, code: ErrorCode, message: str) -> None:
        with self._failure_lock:
            self._failure_code = code
            self._failure_message = message
        if self._on_failure is not None:
            try:
                self._on_failure(code, message)
            except Exception:
                _LOGGER.exception("lease heartbeat failure callback failed")


__all__ = ["LeaseHeartbeat"]
