from __future__ import annotations

import itertools
import time
from dataclasses import dataclass

from app.config import GatewayConfig, RouteTarget
from app.core.errors import GatewayError
from app.services.adapters import ProtocolAdapter, get_adapter


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float | None = None


class ModelRouter:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self._circuits: dict[str, CircuitState] = {}
        self._counters: dict[str, itertools.count] = {}

    def resolve(self, model: str, protocol: str) -> tuple[RouteTarget, ProtocolAdapter]:
        targets = self.candidates(model, protocol)
        return targets[0], get_adapter(protocol)

    def candidates(self, model: str, protocol: str) -> list[RouteTarget]:
        route_config = self.config.models.get(model)
        if route_config is None:
            raise GatewayError(
                f"Unknown model alias: {model}",
                status_code=404,
                error_type="invalid_request_error",
                code="model_not_found",
                param="model",
            )

        matching = [
            route
            for route in route_config.routes
            if protocol in route.protocols
        ]
        if not matching:
            raise GatewayError(
                "Model does not support the requested protocol",
                status_code=422,
                error_type="invalid_request_error",
                code="protocol_not_supported",
                param="model",
            )

        available = [
            route
            for route in matching
            if self.config.providers[route.provider].enabled
            and self._is_available(route.provider)
        ]
        if not available:
            raise GatewayError(
                f"No healthy route supports {protocol!r} for model {model!r}",
                status_code=503,
                error_type="service_unavailable_error",
                code="no_healthy_route",
            )

        if route_config.strategy == "priority":
            return available

        weighted = [route for route in available for _ in range(route.weight)]
        counter = self._counters.setdefault(model, itertools.count())
        offset = next(counter) % len(weighted)
        primary = weighted[offset]
        return [primary, *[route for route in available if route != primary]]

    def record_success(self, provider: str) -> None:
        self._circuits[provider] = CircuitState()

    def record_failure(self, provider: str) -> None:
        state = self._circuits.setdefault(provider, CircuitState())
        state.failures += 1
        if state.failures >= self.config.circuit_breaker.failure_threshold:
            state.opened_at = time.monotonic()

    def _is_available(self, provider: str) -> bool:
        state = self._circuits.get(provider)
        if not state or state.opened_at is None:
            return True
        if (
            time.monotonic() - state.opened_at
            >= self.config.circuit_breaker.cooldown_seconds
        ):
            state.failures = 0
            state.opened_at = None
            return True
        return False

    def status(self) -> dict[str, dict[str, object]]:
        return {
            provider: {
                "failures": state.failures,
                "open": state.opened_at is not None and not self._is_available(provider),
            }
            for provider, state in self._circuits.items()
        }
