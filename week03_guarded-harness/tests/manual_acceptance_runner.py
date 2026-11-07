"""Test-only manual acceptance driver for WP-08 through WP-11."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from fake_model import FakeScriptedModel, final_answer, tool_call

from guarded_harness.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPO_ROOT / "examples"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _prepare(root: Path) -> Path:
    root = root.resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"manual acceptance directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    examples = root / "examples"
    shutil.copytree(EXAMPLES_ROOT, examples)
    subprocess.run(["git", "init", "-q", str(examples)], check=True, capture_output=True)
    _git(examples, "config", "user.email", "manual-acceptance@example.invalid")
    _git(examples, "config", "user.name", "Manual Acceptance")
    _git(examples, "add", ".")
    _git(examples, "commit", "-q", "-m", "manual acceptance baseline")
    print(f"prepared={root}")
    print(f"code_config={examples / 'config' / 'code-understanding.yaml'}")
    print(f"repair_config={examples / 'config' / 'test-repair.yaml'}")
    return examples


def _example_root(root: Path) -> Path:
    examples = root.resolve() / "examples"
    if not (examples / "config").is_dir():
        raise SystemExit(f"run prepare first; missing example tree under {examples}")
    return examples


def _code_document() -> str:
    return """## 事实

- `login_status(True)` 返回 `ok`，未认证时返回 `denied`。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
"""


def _start_code(examples: Path) -> int:
    model = FakeScriptedModel(
        (
            tool_call(
                "manual-code-read",
                "read_file",
                {"path": "src/app.py", "offset": 1, "limit": 20},
            ),
            tool_call(
                "manual-code-write",
                "write_file",
                {
                    "path": "artifacts/code-understanding.md",
                    "content": _code_document(),
                },
            ),
        )
    )
    return main(
        [
            "--config",
            str(examples / "config" / "code-understanding.yaml"),
            "run",
            "--json",
        ],
        model_factory=lambda _path: model,
    )


def _resume_code(examples: Path, run_id: str) -> int:
    model = FakeScriptedModel((final_answer("manual code understanding complete"),))
    return main(
        [
            "--config",
            str(examples / "config" / "code-understanding.yaml"),
            "resume",
            run_id,
            "--json",
        ],
        model_factory=lambda _path: model,
    )


def _start_repair(examples: Path) -> int:
    model = FakeScriptedModel(
        (
            tool_call(
                "manual-repair-patch",
                "apply_patch",
                {
                    "path": "src/calculator.py",
                    "edits": [
                        {
                            "oldText": "return left - right",
                            "newText": "return left + right",
                        }
                    ],
                },
            ),
        )
    )
    return main(
        [
            "--config",
            str(examples / "config" / "test-repair.yaml"),
            "run",
            "--json",
        ],
        model_factory=lambda _path: model,
    )


def _resume_repair(examples: Path, run_id: str) -> int:
    model = FakeScriptedModel((final_answer("manual test repair complete"),))
    return main(
        [
            "--config",
            str(examples / "config" / "test-repair.yaml"),
            "resume",
            run_id,
            "--json",
        ],
        model_factory=lambda _path: model,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("root", type=Path)
    for name in ("start-code", "start-repair"):
        command = commands.add_parser(name)
        command.add_argument("root", type=Path)
    for name in ("resume-code", "resume-repair"):
        command = commands.add_parser(name)
        command.add_argument("root", type=Path)
        command.add_argument("run_id")
    return parser


def main_cli(argv: list[str] | None = None) -> int:
    """Dispatch one manual acceptance helper command."""

    args = _parser().parse_args(argv)
    if args.command == "prepare":
        _prepare(args.root)
        return 0
    examples = _example_root(args.root)
    if args.command == "start-code":
        return _start_code(examples)
    if args.command == "resume-code":
        return _resume_code(examples, args.run_id)
    if args.command == "start-repair":
        return _start_repair(examples)
    if args.command == "resume-repair":
        return _resume_repair(examples, args.run_id)
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
