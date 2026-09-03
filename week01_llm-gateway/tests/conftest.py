from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import GatewayConfig

TEST_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "acceptance.yaml"


def load_acceptance_config(database_url: str | None = None) -> GatewayConfig:
    raw: dict[str, Any] = yaml.safe_load(
        TEST_CONFIG_PATH.read_text(encoding="utf-8")
    )
    if database_url is not None:
        raw["database_url"] = database_url
    return GatewayConfig.model_validate(raw)


@pytest.fixture
def acceptance_config_path() -> Path:
    return TEST_CONFIG_PATH


@pytest.fixture
def acceptance_config(tmp_path: Path) -> GatewayConfig:
    return load_acceptance_config(str(tmp_path / "acceptance.db"))
