"""Strict YAML configuration loading, path normalization, and fingerprinting."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

import yaml
from pydantic import ConfigDict, Field, ValidationError, field_validator

from .domain import (
    ConfigurationError,
    ErrorCode,
    RunBudget,
    Sha256Digest,
    StrictDomainModel,
    TaskKind,
    ValidationIssue,
)

MAX_CONFIG_BYTES = 1_048_576
MAX_YAML_DEPTH = 32
MAX_YAML_NODES = 10_000
_FORBIDDEN_STATE_COMPONENTS = {".git", ".venv", "node_modules", "site-packages", "venv"}
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class _ConfigModel(StrictDomainModel):
    """Base for immutable configuration values."""

    model_config = ConfigDict(frozen=True)


class WorkspaceConfig(_ConfigModel):
    """Workspace identity options from YAML."""

    root: str = Field(min_length=1, max_length=4096)
    require_clean_workspace: bool = True
    protect_other_git_changes: bool = True


class BudgetConfig(_ConfigModel):
    """Resource limits configured for one task."""

    max_steps: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    max_token_cost: Decimal = Field(ge=0)
    max_wall_time_seconds: int = Field(ge=1)

    @field_validator("max_token_cost", mode="before")
    @classmethod
    def _coerce_yaml_integer_decimal(cls, value: object) -> object:
        if isinstance(value, int) and not isinstance(value, bool):
            return Decimal(value)
        return value

    def as_run_budget(self) -> RunBudget:
        """Return the equivalent public run-budget value."""

        return RunBudget(
            max_steps=self.max_steps,
            max_tool_calls=self.max_tool_calls,
            max_token_cost=self.max_token_cost,
            max_wall_time_seconds=self.max_wall_time_seconds,
        )


class PathPolicyConfig(_ConfigModel):
    """Relative POSIX patterns that constrain repository access."""

    deny_read: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = ()
    allow_write: tuple[str, ...] = ()

    @field_validator("deny_read", "deny_write", "allow_write", mode="before")
    @classmethod
    def _coerce_sequence(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class ApprovalConfig(_ConfigModel):
    """Approval channel and tool requirements."""

    required_tools: tuple[str, ...] = ()
    approver_roles: tuple[str, ...] = ()
    ttl_seconds: int = Field(default=900, ge=1, le=86_400)
    channel: Literal["local_cli"] = "local_cli"

    @field_validator("required_tools", "approver_roles", mode="before")
    @classmethod
    def _coerce_sequence(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class CodeUnderstandingAcceptance(_ConfigModel):
    """Contract for code-understanding deliverables."""

    strategy: Literal["code_understanding"]
    deliverables: tuple[str, ...] = Field(min_length=1)

    @field_validator("deliverables", mode="before")
    @classmethod
    def _coerce_deliverables(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class TestRepairAcceptance(_ConfigModel):
    """Minimal contract for test-repair acceptance."""

    strategy: Literal["test_repair"]
    test_command: tuple[str, ...] = Field(min_length=1)
    baseline_required: Literal[True] = True

    @field_validator("test_command", mode="before")
    @classmethod
    def _coerce_test_command(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


AcceptanceConfig = CodeUnderstandingAcceptance | TestRepairAcceptance


class TaskConfig(_ConfigModel):
    """Task declaration consumed by gate, sandbox, and completion policies."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: TaskKind
    description: str = Field(min_length=1, max_length=4000)
    allowed_tools: tuple[str, ...] = Field(min_length=1)
    paths: PathPolicyConfig = Field(default_factory=PathPolicyConfig)
    acceptance: AcceptanceConfig
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_task_kind(cls, value: object) -> object:
        if isinstance(value, str) and value in {kind.value for kind in TaskKind}:
            return TaskKind(value)
        return value

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _coerce_allowed_tools(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class HarnessConfig(_ConfigModel):
    """Top-level versioned YAML configuration."""

    version: Literal[1]
    state_dir: str = Field(min_length=1, max_length=4096)
    workspace: WorkspaceConfig
    budget: BudgetConfig
    task: TaskConfig


class ResolvedHarnessConfig(_ConfigModel):
    """Validated configuration with normalized paths and a stable fingerprint."""

    schema_version: Literal[1] = 1
    config: HarnessConfig
    workspace_root: str = Field(min_length=1, max_length=4096)
    state_dir: str = Field(min_length=1, max_length=4096)
    deliverables: tuple[str, ...] = ()
    fingerprint: Sha256Digest


class _YamlLoadFailure(Exception):
    """Internal YAML failure carrying a stable public error code."""

    def __init__(self, code: ErrorCode, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(message)


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys and parses floats as Decimal."""


def _construct_strict_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            already_present = key in mapping
        except TypeError as exc:
            mark = key_node.start_mark
            raise _YamlLoadFailure(
                ErrorCode.CONFIG_YAML_INVALID,
                f"yaml:{mark.line + 1}:{mark.column + 1}",
                "YAML mapping keys must be hashable",
            ) from exc
        if already_present:
            mark = key_node.start_mark
            raise _YamlLoadFailure(
                ErrorCode.CONFIG_DUPLICATE_KEY,
                f"yaml:{mark.line + 1}:{mark.column + 1}",
                "duplicate YAML mapping key",
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


def _construct_decimal(
    loader: _StrictSafeLoader,
    node: yaml.ScalarNode,
) -> Decimal:
    raw = loader.construct_scalar(node)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise _YamlLoadFailure(
            ErrorCode.CONFIG_YAML_INVALID,
            f"yaml:{node.start_mark.line + 1}:{node.start_mark.column + 1}",
            "invalid decimal value",
        ) from exc
    if not value.is_finite():
        raise _YamlLoadFailure(
            ErrorCode.CONFIG_YAML_INVALID,
            f"yaml:{node.start_mark.line + 1}:{node.start_mark.column + 1}",
            "non-finite numbers are not supported",
        )
    return value


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_strict_mapping,
)
_StrictSafeLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def _validate_yaml_complexity(root: object) -> None:
    """Bound alias-expanded YAML traversal by depth and unique node count."""

    active: set[int] = set()
    visited: set[int] = set()
    nodes_seen = 0

    def visit(value: object, depth: int) -> None:
        nonlocal nodes_seen
        if depth > MAX_YAML_DEPTH:
            raise _YamlLoadFailure(
                ErrorCode.CONFIG_YAML_INVALID,
                "$",
                "YAML nesting exceeds the supported depth",
            )
        if not isinstance(value, (dict, list)):
            return
        identity = id(value)
        if identity in active:
            raise _YamlLoadFailure(
                ErrorCode.CONFIG_YAML_INVALID,
                "$",
                "recursive YAML aliases are not supported",
            )
        if identity in visited:
            return
        active.add(identity)
        visited.add(identity)
        nodes_seen += 1
        if nodes_seen > MAX_YAML_NODES:
            raise _YamlLoadFailure(
                ErrorCode.CONFIG_TOO_LARGE,
                "$",
                "YAML contains too many nodes",
            )
        children = value.values() if isinstance(value, dict) else value
        for child in children:
            visit(child, depth + 1)
        active.remove(identity)

    visit(root, 0)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise ConfigurationError(ErrorCode.CONFIG_FILE_NOT_FOUND) from exc
    except OSError as exc:
        raise ConfigurationError(ErrorCode.CONFIG_IO_ERROR) from exc
    if not path.is_file():
        raise ConfigurationError(ErrorCode.CONFIG_IO_ERROR)
    if size > MAX_CONFIG_BYTES:
        raise ConfigurationError(ErrorCode.CONFIG_TOO_LARGE)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(ErrorCode.CONFIG_YAML_INVALID) from exc
    except OSError as exc:
        raise ConfigurationError(ErrorCode.CONFIG_IO_ERROR) from exc

    try:
        documents = list(yaml.load_all(text, Loader=_StrictSafeLoader))
    except _YamlLoadFailure as exc:
        issue = ValidationIssue(code=exc.code, path=exc.path, message=exc.message)
        raise ConfigurationError(exc.code, (issue,)) from exc
    except yaml.YAMLError as exc:
        issue = ValidationIssue(
            code=ErrorCode.CONFIG_YAML_INVALID,
            path="$",
            message="YAML parsing failed",
        )
        raise ConfigurationError(ErrorCode.CONFIG_YAML_INVALID, (issue,)) from exc

    if len(documents) > 1:
        issue = ValidationIssue(
            code=ErrorCode.CONFIG_MULTIPLE_DOCUMENTS,
            path="$",
            message="exactly one YAML document is supported",
        )
        raise ConfigurationError(ErrorCode.CONFIG_MULTIPLE_DOCUMENTS, (issue,))
    if not documents:
        issue = ValidationIssue(
            code=ErrorCode.CONFIG_YAML_INVALID,
            path="$",
            message="YAML document is empty",
        )
        raise ConfigurationError(ErrorCode.CONFIG_YAML_INVALID, (issue,))
    root = documents[0]
    if not isinstance(root, dict):
        issue = ValidationIssue(
            code=ErrorCode.CONFIG_ROOT_NOT_MAPPING,
            path="$",
            message="YAML root must be a mapping",
        )
        raise ConfigurationError(ErrorCode.CONFIG_ROOT_NOT_MAPPING, (issue,))
    try:
        _validate_yaml_complexity(root)
    except _YamlLoadFailure as exc:
        issue = ValidationIssue(code=exc.code, path=exc.path, message=exc.message)
        raise ConfigurationError(exc.code, (issue,)) from exc
    if not all(isinstance(key, str) for key in root):
        issue = ValidationIssue(
            code=ErrorCode.CONFIG_YAML_INVALID,
            path="$",
            message="top-level configuration keys must be strings",
        )
        raise ConfigurationError(ErrorCode.CONFIG_YAML_INVALID, (issue,))
    return root


def _format_location(location: tuple[int | str, ...]) -> str:
    if not location:
        return "$"
    rendered = ""
    for part in location:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else str(part)
    return rendered


def _config_issues_from_pydantic(exc: ValidationError) -> tuple[ValidationIssue, ...]:
    issues = []
    for error in exc.errors(include_input=False, include_url=False)[:100]:
        issues.append(
            ValidationIssue(
                code=ErrorCode.CONFIG_SCHEMA_INVALID,
                path=_format_location(tuple(error.get("loc", ()))),
                message=str(error.get("msg", "invalid configuration value"))[:1000],
            )
        )
    return tuple(issues)


def _issue(code: ErrorCode, path: str, message: str) -> ConfigurationError:
    return ConfigurationError(code, (ValidationIssue(code=code, path=path, message=message),))


def _resolve_candidate(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        return path.resolve(strict=True)
    ancestor = path.parent
    while not ancestor.exists() and not ancestor.is_symlink():
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    resolved_ancestor = ancestor.resolve(strict=True)
    tail = path.relative_to(ancestor)
    return resolved_ancestor.joinpath(tail).resolve(strict=False)


def _resolve_workspace_root(raw: str, base_dir: Path) -> Path:
    if "\x00" in raw:
        raise _issue(ErrorCode.CONFIG_PATH_INVALID, "workspace.root", "path contains NUL")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    try:
        resolved = _resolve_candidate(candidate)
    except (OSError, RuntimeError) as exc:
        raise _issue(
            ErrorCode.CONFIG_PATH_INVALID,
            "workspace.root",
            "workspace root cannot be resolved",
        ) from exc
    if not resolved.exists() or not resolved.is_dir():
        raise _issue(
            ErrorCode.CONFIG_PATH_INVALID,
            "workspace.root",
            "workspace root must be an existing directory",
        )
    return resolved


def _resolve_state_dir(raw: str, base_dir: Path, workspace_root: Path) -> Path:
    if "\x00" in raw:
        raise _issue(ErrorCode.CONFIG_PATH_INVALID, "state_dir", "path contains NUL")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    try:
        resolved = _resolve_candidate(candidate)
    except (OSError, RuntimeError) as exc:
        raise _issue(
            ErrorCode.CONFIG_PATH_INVALID,
            "state_dir",
            "state directory cannot be resolved",
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise _issue(ErrorCode.CONFIG_PATH_INVALID, "state_dir", "state_dir must be a directory")
    if resolved.parent == resolved:
        raise _issue(
            ErrorCode.CONFIG_PATH_INVALID,
            "state_dir",
            "state_dir cannot be a filesystem root",
        )
    if any(part.casefold() in _FORBIDDEN_STATE_COMPONENTS for part in resolved.parts):
        raise _issue(
            ErrorCode.CONFIG_PATH_INVALID,
            "state_dir",
            "state_dir cannot be inside a protected dependency or Git directory",
        )
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        pass
    else:
        raise _issue(
            ErrorCode.CONFIG_PATH_INVALID,
            "state_dir",
            "state_dir must be outside workspace_root",
        )
    return resolved


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _has_unsafe_filename_component(value: str) -> bool:
    for part in PurePosixPath(value).parts:
        if part.endswith((".", " ")):
            return True
        base_name = part.rstrip(" .").split(".", 1)[0].casefold()
        if base_name in _WINDOWS_RESERVED_NAMES:
            return True
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            return True
    return False


def _normalize_patterns(
    values: tuple[str, ...],
    *,
    field_path: str,
    reject_platform_sensitive: bool = False,
) -> tuple[str, ...]:
    normalized: list[str] = []
    keys: set[str] = set()
    for index, raw in enumerate(values):
        if (
            not raw
            or raw != raw.strip()
            or "\\" in raw
            or PurePosixPath(raw).is_absolute()
            or PureWindowsPath(raw).is_absolute()
            or PureWindowsPath(raw).drive
            or ".." in PurePosixPath(raw).parts
            or ":" in raw
            or _has_unsafe_filename_component(raw)
        ):
            raise _issue(
                ErrorCode.CONFIG_PATH_INVALID,
                f"{field_path}[{index}]",
                "path pattern must be a relative POSIX path",
            )
        parts = [part for part in PurePosixPath(raw).parts if part != "."]
        if not parts:
            raise _issue(
                ErrorCode.CONFIG_PATH_INVALID,
                f"{field_path}[{index}]",
                "path pattern cannot be empty",
            )
        if reject_platform_sensitive:
            lowered_parts = [part.casefold() for part in parts]
            final_part = lowered_parts[-1]
            if (
                ".git" in lowered_parts
                or final_part == ".env"
                or final_part.startswith(".env.")
                or final_part.endswith((".pem", ".key", ".p12", ".pfx"))
                or final_part in {"credentials", "credentials.json", "id_rsa", "id_ed25519"}
            ):
                raise _issue(
                    ErrorCode.CONFIG_PATH_INVALID,
                    f"{field_path}[{index}]",
                    "allow_write cannot target a platform-sensitive path",
                )
        value = PurePosixPath(*parts).as_posix()
        key = _path_key(value)
        if key in keys:
            raise _issue(
                ErrorCode.CONFIG_SCHEMA_INVALID,
                f"{field_path}[{index}]",
                "path pattern is duplicated",
            )
        keys.add(key)
        normalized.append(value)
    return tuple(sorted(normalized, key=str.casefold))


def _normalize_deliverables(
    values: tuple[str, ...],
    *,
    workspace_root: Path,
    allow_write: tuple[str, ...],
) -> tuple[str, ...]:
    normalized: list[str] = []
    keys: set[str] = set()
    for index, raw in enumerate(values):
        path = PurePosixPath(raw)
        if (
            not raw
            or raw != raw.strip()
            or "\\" in raw
            or path.is_absolute()
            or PureWindowsPath(raw).is_absolute()
            or PureWindowsPath(raw).drive
            or ".." in path.parts
            or not path.parts
            or ":" in raw
            or _has_unsafe_filename_component(raw)
        ):
            raise _issue(
                ErrorCode.CONFIG_PATH_ESCAPE,
                f"task.acceptance.deliverables[{index}]",
                "deliverable must be a non-escaping relative path",
            )
        relative_input = Path(*path.parts)
        try:
            resolved = _resolve_candidate(workspace_root / relative_input)
            relative = resolved.relative_to(workspace_root).as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _issue(
                ErrorCode.CONFIG_PATH_ESCAPE,
                f"task.acceptance.deliverables[{index}]",
                "deliverable resolves outside the workspace",
            ) from exc
        if relative in {"", "."}:
            raise _issue(
                ErrorCode.CONFIG_PATH_INVALID,
                f"task.acceptance.deliverables[{index}]",
                "deliverable must name a file",
            )
        relative_parts = {part.casefold() for part in PurePosixPath(relative).parts}
        file_name = PurePosixPath(relative).name.casefold()
        if (
            ".git" in relative_parts
            or file_name == ".env"
            or file_name.startswith(".env.")
            or file_name.endswith((".pem", ".key", ".p12", ".pfx"))
            or file_name in {"credentials", "credentials.json", "id_rsa", "id_ed25519"}
        ):
            raise _issue(
                ErrorCode.CONFIG_PATH_INVALID,
                f"task.acceptance.deliverables[{index}]",
                "deliverable cannot target a platform-sensitive path",
            )
        allowed = any(
            fnmatch.fnmatchcase(relative, pattern) or PurePosixPath(relative).match(pattern)
            for pattern in allow_write
        )
        if not allowed:
            raise _issue(
                ErrorCode.CONFIG_PATH_INVALID,
                f"task.acceptance.deliverables[{index}]",
                "deliverable must match task.paths.allow_write",
            )
        key = _path_key(relative)
        if key in keys:
            raise _issue(
                ErrorCode.CONFIG_DUPLICATE_DELIVERABLE,
                f"task.acceptance.deliverables[{index}]",
                "deliverable is duplicated after normalization",
            )
        keys.add(key)
        normalized.append(relative)
    return tuple(normalized)


def _normalized_string_tuple(
    values: tuple[str, ...],
    *,
    field_path: str,
    require_tool_names: bool = False,
) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for index, raw in enumerate(values):
        if not raw or raw != raw.strip():
            raise _issue(
                ErrorCode.CONFIG_SCHEMA_INVALID,
                f"{field_path}[{index}]",
                "value must be a non-empty trimmed string",
            )
        if require_tool_names and re.fullmatch(r"[a-z][a-z0-9_]*", raw) is None:
            raise _issue(
                ErrorCode.CONFIG_SCHEMA_INVALID,
                f"{field_path}[{index}]",
                "tool name contains unsupported characters",
            )
        key = raw.casefold()
        if key in seen:
            raise _issue(
                ErrorCode.CONFIG_SCHEMA_INVALID,
                f"{field_path}[{index}]",
                "value is duplicated",
            )
        seen.add(key)
        normalized.append(raw)
    return tuple(sorted(normalized, key=str.casefold))


def _validate_semantics(config: HarnessConfig) -> None:
    if not config.task.allowed_tools:
        raise _issue(
            ErrorCode.CONFIG_SCHEMA_INVALID,
            "task.allowed_tools",
            "at least one allowed tool is required",
        )
    if config.task.kind == TaskKind.CODE_UNDERSTANDING:
        if not isinstance(config.task.acceptance, CodeUnderstandingAcceptance):
            raise _issue(
                ErrorCode.CONFIG_KIND_STRATEGY_MISMATCH,
                "task.acceptance.strategy",
                "code_understanding task requires code_understanding acceptance",
            )
    elif not isinstance(config.task.acceptance, TestRepairAcceptance):
        raise _issue(
            ErrorCode.CONFIG_KIND_STRATEGY_MISMATCH,
            "task.acceptance.strategy",
            "test_repair task requires test_repair acceptance",
        )
    if any(not part for part in config.task.allowed_tools):
        raise _issue(
            ErrorCode.CONFIG_SCHEMA_INVALID,
            "task.allowed_tools",
            "tool names must be non-empty",
        )
    for path, values in (
        ("task.allowed_tools", config.task.allowed_tools),
        ("task.approval.required_tools", config.task.approval.required_tools),
    ):
        if len({value.casefold() for value in values}) != len(values):
            raise _issue(
                ErrorCode.CONFIG_SCHEMA_INVALID,
                path,
                "tool names must not contain duplicates",
            )
    required = {tool.casefold() for tool in config.task.approval.required_tools}
    allowed = {tool.casefold() for tool in config.task.allowed_tools}
    if not required.issubset(allowed):
        raise _issue(
            ErrorCode.CONFIG_APPROVAL_INVALID,
            "task.approval.required_tools",
            "required approval tools must also be allowed tools",
        )
    if required and not config.task.approval.approver_roles:
        raise _issue(
            ErrorCode.CONFIG_APPROVAL_INVALID,
            "task.approval.approver_roles",
            "approver_roles is required when approval-required tools are configured",
        )
    if len({role.casefold() for role in config.task.approval.approver_roles}) != len(
        config.task.approval.approver_roles
    ):
        raise _issue(
            ErrorCode.CONFIG_APPROVAL_INVALID,
            "task.approval.approver_roles",
            "approver roles must not contain duplicates",
        )
    if config.task.approval.channel != "local_cli":
        raise _issue(
            ErrorCode.CONFIG_APPROVAL_INVALID,
            "task.approval.channel",
            "only the local_cli approval channel is supported",
        )
    if isinstance(config.task.acceptance, TestRepairAcceptance):
        if not config.task.acceptance.test_command:
            raise _issue(
                ErrorCode.CONFIG_SCHEMA_INVALID,
                "task.acceptance.test_command",
                "test command cannot be empty",
            )
        if any(not part or part != part.strip() for part in config.task.acceptance.test_command):
            raise _issue(
                ErrorCode.CONFIG_SCHEMA_INVALID,
                "task.acceptance.test_command",
                "test command arguments must be non-empty trimmed strings",
            )


def _normalize_config(config: HarnessConfig, base_dir: Path) -> ResolvedHarnessConfig:
    workspace_root = _resolve_workspace_root(config.workspace.root, base_dir)
    state_dir = _resolve_state_dir(config.state_dir, base_dir, workspace_root)
    deny_read = _normalize_patterns(config.task.paths.deny_read, field_path="task.paths.deny_read")
    deny_write = _normalize_patterns(
        config.task.paths.deny_write,
        field_path="task.paths.deny_write",
    )
    allow_write = _normalize_patterns(
        config.task.paths.allow_write,
        field_path="task.paths.allow_write",
        reject_platform_sensitive=True,
    )
    allowed_tools = _normalized_string_tuple(
        config.task.allowed_tools,
        field_path="task.allowed_tools",
        require_tool_names=True,
    )
    required_tools = _normalized_string_tuple(
        config.task.approval.required_tools,
        field_path="task.approval.required_tools",
        require_tool_names=True,
    )
    approver_roles = _normalized_string_tuple(
        config.task.approval.approver_roles,
        field_path="task.approval.approver_roles",
    )

    if isinstance(config.task.acceptance, CodeUnderstandingAcceptance):
        deliverables = _normalize_deliverables(
            config.task.acceptance.deliverables,
            workspace_root=workspace_root,
            allow_write=allow_write,
        )
        acceptance = CodeUnderstandingAcceptance(
            strategy="code_understanding",
            deliverables=deliverables,
        )
    else:
        deliverables = ()
        acceptance = TestRepairAcceptance(
            strategy="test_repair",
            test_command=config.task.acceptance.test_command,
            baseline_required=True,
        )

    normalized_task = TaskConfig(
        id=config.task.id,
        kind=config.task.kind,
        description=config.task.description,
        allowed_tools=allowed_tools,
        paths=PathPolicyConfig(
            deny_read=deny_read,
            deny_write=deny_write,
            allow_write=allow_write,
        ),
        acceptance=acceptance,
        approval=ApprovalConfig(
            required_tools=required_tools,
            approver_roles=approver_roles,
            ttl_seconds=config.task.approval.ttl_seconds,
            channel=config.task.approval.channel,
        ),
    )
    normalized_config = HarnessConfig(
        version=1,
        state_dir=str(state_dir),
        workspace=WorkspaceConfig(
            root=str(workspace_root),
            require_clean_workspace=config.workspace.require_clean_workspace,
            protect_other_git_changes=config.workspace.protect_other_git_changes,
        ),
        budget=config.budget,
        task=normalized_task,
    )
    fingerprint = _compute_fingerprint(normalized_config)
    return ResolvedHarnessConfig(
        config=normalized_config,
        workspace_root=str(workspace_root),
        state_dir=str(state_dir),
        deliverables=deliverables,
        fingerprint=fingerprint,
    )


def _compute_fingerprint(config: HarnessConfig) -> str:
    try:
        payload = {
            "fingerprint_schema": 1,
            "config": config.model_dump(mode="json"),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConfigurationError(ErrorCode.CONFIG_FINGERPRINT_ERROR) from exc
    return f"sha256:{digest}"


def config_fingerprint(config: ResolvedHarnessConfig) -> str:
    """Recompute the canonical SHA-256 fingerprint for resolved configuration."""

    return _compute_fingerprint(config.config)


def load_config(path: str | Path) -> ResolvedHarnessConfig:
    """Load, validate, normalize, and fingerprint a single YAML task configuration."""

    config_path = Path(path)
    try:
        resolved_path = config_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ConfigurationError(ErrorCode.CONFIG_FILE_NOT_FOUND) from exc
    except OSError as exc:
        raise ConfigurationError(ErrorCode.CONFIG_IO_ERROR) from exc
    payload = _load_yaml_mapping(resolved_path)
    try:
        config = HarnessConfig.model_validate(payload, strict=True)
    except ValidationError as exc:
        issues = _config_issues_from_pydantic(exc)
        code = issues[0].code if issues else ErrorCode.CONFIG_SCHEMA_INVALID
        raise ConfigurationError(code, issues) from exc
    _validate_semantics(config)
    return _normalize_config(config, resolved_path.parent)


__all__ = [
    "ApprovalConfig",
    "BudgetConfig",
    "CodeUnderstandingAcceptance",
    "HarnessConfig",
    "PathPolicyConfig",
    "ResolvedHarnessConfig",
    "TaskConfig",
    "TestRepairAcceptance",
    "WorkspaceConfig",
    "config_fingerprint",
    "load_config",
]
