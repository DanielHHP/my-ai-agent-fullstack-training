from __future__ import annotations

import json

from rag.cli import main
from rag.config import DEFAULT_CORPUS


def test_bm25_build_and_search_are_offline(tmp_path, capsys) -> None:
    store = tmp_path / "store"

    assert (
        main(
            [
                "build",
                "--backend",
                "bm25",
                "--corpus",
                str(DEFAULT_CORPUS),
                "--store",
                str(store),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "search",
                "支付会话有效期为 5 分钟",
                "--mode",
                "bm25",
                "--top-k",
                "3",
                "--store",
                str(store),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "bm25"
    assert payload["result_count"] > 0
    assert payload["results"][0]["file"] == "docs/payment-session.md"
    assert payload["results"][0]["score_type"] == "bm25"


def test_build_refuses_nonempty_store_without_overwrite(tmp_path, capsys) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "keep.txt").write_text("keep", encoding="utf-8")

    assert (
        main(
            [
                "build",
                "--backend",
                "bm25",
                "--corpus",
                str(DEFAULT_CORPUS),
                "--store",
                str(store),
            ]
        )
        == 1
    )
    assert "--overwrite" in capsys.readouterr().err


def test_failed_vector_build_preserves_existing_store(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    marker = store / "manifest.json"
    marker.write_text('{"schema_version": 1}', encoding="utf-8")

    import rag.indexer as indexer

    def fail_embedding():
        raise RuntimeError("simulated embedding outage")

    monkeypatch.setattr(indexer, "create_embedding_model", fail_embedding)

    assert (
        main(
            [
                "build",
                "--backend",
                "vector",
                "--corpus",
                str(DEFAULT_CORPUS),
                "--store",
                str(store),
                "--overwrite",
            ]
        )
        == 1
    )

    assert marker.read_text(encoding="utf-8") == '{"schema_version": 1}'
    assert "simulated embedding outage" in capsys.readouterr().err
