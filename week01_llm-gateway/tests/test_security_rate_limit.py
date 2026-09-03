from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import GatewayConfig, RateLimitConfig
from app.core.errors import GatewayError
from app.core.rate_limit import InMemoryRateLimiter
from app.core.security import authenticate, key_fingerprint


def _config(api_keys: list[str] | None = None) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "api_keys": api_keys or [],
            "providers": {
                "openai": {
                    "base_url": "https://openai.test",
                    "api_key": "openai-key",
                }
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "openai",
                            "model": "gpt-primary",
                            "api": "chat",
                        }
                    ],
                }
            },
        }
    )


def _request(config: GatewayConfig) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def test_key_fingerprint_is_short_and_stable() -> None:
    first = key_fingerprint("secret-key")
    second = key_fingerprint("secret-key")

    assert first == second
    assert len(first) == 16
    assert "secret-key" not in first


@pytest.mark.asyncio
async def test_authenticate_uses_client_fingerprint_in_dev_mode() -> None:
    request = _request(_config())

    identity = await authenticate(request)

    assert identity == key_fingerprint("127.0.0.1")


@pytest.mark.asyncio
async def test_authenticate_accepts_bearer_key() -> None:
    request = _request(_config(["secret-key"]))

    identity = await authenticate(
        request,
        authorization="Bearer secret-key",
        x_api_key=None,
    )

    assert identity == key_fingerprint("secret-key")


@pytest.mark.asyncio
async def test_authenticate_accepts_x_api_key() -> None:
    request = _request(_config(["secret-key"]))

    identity = await authenticate(
        request,
        authorization=None,
        x_api_key="secret-key",
    )

    assert identity == key_fingerprint("secret-key")


@pytest.mark.asyncio
async def test_authenticate_rejects_invalid_key() -> None:
    request = _request(_config(["secret-key"]))

    with pytest.raises(GatewayError) as exc_info:
        await authenticate(request, authorization="Bearer wrong")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


@pytest.mark.asyncio
async def test_rate_limiter_rejects_after_burst_exhausted() -> None:
    limiter = InMemoryRateLimiter(
        RateLimitConfig(enabled=True, requests_per_minute=60, burst=2)
    )

    await limiter.check("caller-1")
    await limiter.check("caller-1")

    with pytest.raises(GatewayError) as exc_info:
        await limiter.check("caller-1")

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "rate_limit_exceeded"
    assert exc_info.value.details["retry_after"] >= 1
