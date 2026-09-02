from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import GatewayConfig


@dataclass
class UsageEvent:
    request_id: str
    api_key_hash: str
    protocol: str
    endpoint: str
    requested_model: str
    provider: str | None = None
    upstream_model: str | None = None
    stream: bool = False
    status: str = "success"
    status_code: int = 200
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    first_token_ms: float | None = None
    retries: int = 0
    fallbacks: int = 0
    repair_retries: int = 0
    retry_input_tokens: int = 0
    retry_output_tokens: int = 0
    retry_cached_tokens: int = 0
    retry_cost_usd: float = 0.0
    error_type: str | None = None
    error_message: str | None = None
    prompt_id: str | None = None
    prompt_version: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class UsageRepository:
    """Persists call usage and cost data to SQLite."""

    def __init__(self, database_path: str, config: GatewayConfig) -> None:
        self.database_path = database_path
        self.config = config

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    api_key_hash TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    provider TEXT,
                    upstream_model TEXT,
                    stream INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cached_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    latency_ms REAL NOT NULL,
                    first_token_ms REAL,
                    retries INTEGER NOT NULL,
                    fallbacks INTEGER NOT NULL,
                    repair_retries INTEGER NOT NULL,
                    retry_input_tokens INTEGER NOT NULL,
                    retry_output_tokens INTEGER NOT NULL,
                    retry_cached_tokens INTEGER NOT NULL,
                    retry_cost_usd REAL NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    prompt_id TEXT,
                    prompt_version INTEGER,
                    metadata_json TEXT
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_events(created_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_requested_model ON usage_events(requested_model)"
            )
            await db.commit()

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
    ) -> float:
        price = self.config.pricing.get(model)
        if price is None:
            return 0.0

        fresh_input = max(0, input_tokens - cached_tokens)
        cached_rate = price.cached_input_per_million
        if cached_rate is None:
            cached_rate = price.input_per_million

        cost = fresh_input * price.input_per_million / 1_000_000
        cost += cached_tokens * cached_rate / 1_000_000
        cost += output_tokens * price.output_per_million / 1_000_000
        return round(cost, 10)

    async def record(self, event: UsageEvent) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        metadata_json = (
            json.dumps(event.metadata, ensure_ascii=False) if event.metadata else None
        )

        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO usage_events (
                    request_id, created_at, api_key_hash, protocol, endpoint,
                    requested_model, provider, upstream_model, stream, status,
                    status_code, input_tokens, output_tokens, cached_tokens,
                    cost_usd, latency_ms, first_token_ms, retries, fallbacks,
                    repair_retries, retry_input_tokens, retry_output_tokens,
                    retry_cached_tokens, retry_cost_usd, error_type,
                    error_message, prompt_id, prompt_version, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.request_id,
                    created_at,
                    event.api_key_hash,
                    event.protocol,
                    event.endpoint,
                    event.requested_model,
                    event.provider,
                    event.upstream_model,
                    int(event.stream),
                    event.status,
                    event.status_code,
                    event.input_tokens,
                    event.output_tokens,
                    event.cached_tokens,
                    event.cost_usd,
                    event.latency_ms,
                    event.first_token_ms,
                    event.retries,
                    event.fallbacks,
                    event.repair_retries,
                    event.retry_input_tokens,
                    event.retry_output_tokens,
                    event.retry_cached_tokens,
                    event.retry_cost_usd,
                    event.error_type,
                    event.error_message,
                    event.prompt_id,
                    event.prompt_version,
                    metadata_json,
                ),
            )
            await db.commit()

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM usage_events ORDER BY created_at DESC LIMIT ?",
                (min(limit, 1000),),
            )
            rows = await cursor.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            metadata_json = item.pop("metadata_json", None)
            item["metadata"] = json.loads(metadata_json) if metadata_json else {}
            results.append(item)
        return results


__all__ = ["UsageEvent", "UsageRepository"]
