"""Subprocess helper that injects a deterministic model into the real CLI main."""

from __future__ import annotations

import sys

from fake_model import FakeScriptedModel, final_answer

from guarded_harness.cli import main


def runner_model_factory(_config_path):
    return FakeScriptedModel((final_answer("subprocess resume complete"),))


if __name__ == "__main__":
    config_path, run_id = sys.argv[1:3]
    raise SystemExit(
        main(
            [
                "--config",
                config_path,
                "resume",
                run_id,
                "--json",
            ],
            model_factory=runner_model_factory,
        )
    )
