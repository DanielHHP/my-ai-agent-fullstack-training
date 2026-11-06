"""Trusted repository tool assembly."""

from __future__ import annotations

from ..config import ResolvedHarnessConfig, TestRepairAcceptance
from ..domain import (
    ApprovalRequirement,
    ConcurrencyClass,
    ErrorCode,
    RetryPolicy,
    ToolOutputKind,
    ToolPermission,
    ToolRisk,
    ToolSpec,
)
from ..registry import ToolDefinition, ToolRegistry, ToolRegistryError
from ..sandbox import Sandbox
from .common import (
    DIFF_OUTPUT_BYTES,
    LIST_OUTPUT_BYTES,
    READ_OUTPUT_BYTES,
    SEARCH_OUTPUT_BYTES,
    STATUS_OUTPUT_BYTES,
    TEST_OUTPUT_BYTES,
    WRITE_OUTPUT_BYTES,
    file_content_schema,
    integer_schema,
    normalize_git_diff,
    normalize_list_files,
    normalize_read_directory,
    normalize_read_file,
    normalize_search_code,
    object_schema,
    patch_edit_schema,
    text_schema,
)
from .exec import ACCEPTANCE_TEST_COMMAND_ID, run_test_handler
from .fs_read import (
    list_files_handler,
    read_directory_handler,
    read_file_handler,
    search_code_handler,
)
from .fs_write import (
    apply_patch_handler,
    apply_patch_normalizer,
    write_file_handler,
    write_file_normalizer,
)
from .git import git_command_specs, git_diff_handler, git_status_handler

_READ_PERMISSIONS = frozenset({ToolPermission.READ})
_PATH_SCHEMA = text_schema(max_length=4_096)
_EMPTY_SCHEMA = object_schema({}, ())


def register_readonly_tools(
    registry: ToolRegistry,
    sandbox: Sandbox,
) -> tuple[str, ...]:
    """Register all trusted read-only tools and their fixed Git commands."""

    definitions = _build_definitions(sandbox)
    names = tuple(definition.spec.name for definition in definitions)
    conflicts = [name for name in names if registry.contains(name)]
    if conflicts:
        raise ToolRegistryError(
            ErrorCode.REGISTRY_TOOL_CONFLICT,
            "one or more read-only tool names are already registered",
        )
    for command in git_command_specs():
        sandbox.register_trusted_command(command)
    for definition in definitions:
        registry.register(definition)
    return names


def _build_definitions(sandbox: Sandbox) -> tuple[ToolDefinition, ...]:
    definitions = (
        ToolDefinition(
            spec=_spec(
                name="git_diff",
                description=(
                    "Show the current workspace diff, or the staged diff, limited to one "
                    "workspace-relative path. Denied paths are omitted."
                ),
                input_schema=object_schema(
                    {
                        "path": _PATH_SCHEMA,
                        "staged": {"type": "boolean"},
                    },
                    ("path",),
                ),
                timeout_seconds=15,
                path_arguments=("path",),
                output_limit_bytes=DIFF_OUTPUT_BYTES + 4_096,
            ),
            handler=git_diff_handler(sandbox),
            normalize_args=normalize_git_diff,
        ),
        ToolDefinition(
            spec=_spec(
                name="git_status",
                description=(
                    "Show bounded, policy-filtered Git workspace status without changing "
                    "the index or workspace."
                ),
                input_schema=_EMPTY_SCHEMA,
                timeout_seconds=10,
                output_limit_bytes=STATUS_OUTPUT_BYTES + 4_096,
            ),
            handler=git_status_handler(sandbox),
        ),
        ToolDefinition(
            spec=_spec(
                name="list_files",
                description=(
                    "List regular repository files matching a workspace-relative glob. "
                    "Dependency, cache, build and denied directories are skipped."
                ),
                input_schema=object_schema(
                    {
                        "path": _PATH_SCHEMA,
                        "pattern": text_schema(),
                        "limit": integer_schema(minimum=1, maximum=500),
                    },
                    ("path", "pattern"),
                ),
                timeout_seconds=8,
                path_arguments=("path",),
                output_limit_bytes=LIST_OUTPUT_BYTES + 4_096,
            ),
            handler=list_files_handler(sandbox),
            normalize_args=normalize_list_files,
        ),
        ToolDefinition(
            spec=_spec(
                name="read_directory",
                description=(
                    "List one directory level with stable ordering. Directory symlinks "
                    "are not followed and denied entries are omitted."
                ),
                input_schema=object_schema(
                    {
                        "path": _PATH_SCHEMA,
                        "limit": integer_schema(minimum=1, maximum=500),
                    },
                    ("path",),
                ),
                timeout_seconds=5,
                path_arguments=("path",),
                output_limit_bytes=LIST_OUTPUT_BYTES + 4_096,
            ),
            handler=read_directory_handler(sandbox),
            normalize_args=normalize_read_directory,
        ),
        ToolDefinition(
            spec=_spec(
                name="read_file",
                description=(
                    "Read a bounded UTF-8 text window with line numbers and a content "
                    "digest. Use next_offset to continue."
                ),
                input_schema=object_schema(
                    {
                        "path": _PATH_SCHEMA,
                        "offset": integer_schema(minimum=1, maximum=2_147_483_647),
                        "limit": integer_schema(minimum=1, maximum=2_000),
                    },
                    ("path",),
                ),
                timeout_seconds=5,
                path_arguments=("path",),
                output_limit_bytes=READ_OUTPUT_BYTES + 4_096,
            ),
            handler=read_file_handler(sandbox),
            normalize_args=normalize_read_file,
        ),
        ToolDefinition(
            spec=_spec(
                name="search_code",
                description=(
                    "Search text files using a literal string and optional case folding. "
                    "Returns bounded matches with line numbers, context and content digests."
                ),
                input_schema=object_schema(
                    {
                        "path": _PATH_SCHEMA,
                        "pattern": text_schema(),
                        "glob": text_schema(),
                        "ignore_case": {"type": "boolean"},
                        "context": integer_schema(minimum=0, maximum=10),
                        "limit": integer_schema(minimum=1, maximum=200),
                    },
                    ("path", "pattern"),
                ),
                timeout_seconds=15,
                path_arguments=("path",),
                output_limit_bytes=SEARCH_OUTPUT_BYTES + 4_096,
            ),
            handler=search_code_handler(sandbox),
            normalize_args=normalize_search_code,
        ),
    )
    return tuple(sorted(definitions, key=lambda definition: definition.spec.name))


def _spec(
    *,
    name: str,
    description: str,
    input_schema: dict,
    timeout_seconds: int,
    output_limit_bytes: int,
    path_arguments: tuple[str, ...] = (),
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema=input_schema,
        output_kind=ToolOutputKind.JSON,
        risk=ToolRisk.READ,
        required_permissions=_READ_PERMISSIONS,
        timeout_seconds=timeout_seconds,
        retry_policy=RetryPolicy.SAFE_READ_ONLY,
        idempotent=True,
        path_arguments=path_arguments,
        output_limit_bytes=output_limit_bytes,
        approval_requirement=ApprovalRequirement.NEVER,
        concurrency_class=ConcurrencyClass.SERIAL,
    )


def register_side_effect_tools(
    registry: ToolRegistry,
    sandbox: Sandbox,
    config: ResolvedHarnessConfig,
) -> tuple[str, ...]:
    """Register trusted write and command tools for the configured task."""

    definitions = _build_side_effect_definitions(sandbox, config)
    names = tuple(definition.spec.name for definition in definitions)
    conflicts = [name for name in names if registry.contains(name)]
    if conflicts:
        raise ToolRegistryError(
            ErrorCode.REGISTRY_TOOL_CONFLICT,
            "one or more side-effect tool names are already registered",
        )
    for definition in definitions:
        registry.register(definition)
    return names


def _build_side_effect_definitions(
    sandbox: Sandbox,
    config: ResolvedHarnessConfig,
) -> tuple[ToolDefinition, ...]:
    definitions: list[ToolDefinition] = [
        ToolDefinition(
            spec=_side_effect_spec(
                name="apply_patch",
                description=(
                    "Apply one or more exact text replacements to one existing UTF-8 file. "
                    "Every oldText must match once; the whole batch succeeds or writes nothing."
                ),
                input_schema=object_schema(
                    {
                        "path": _PATH_SCHEMA,
                        "edits": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 100,
                            "items": patch_edit_schema(),
                        },
                    },
                    ("path", "edits"),
                ),
                risk=ToolRisk.WRITE,
                permission=ToolPermission.WRITE,
                timeout_seconds=5,
                path_arguments=("path",),
                output_limit_bytes=WRITE_OUTPUT_BYTES,
            ),
            handler=apply_patch_handler(sandbox, config),
            normalize_args=apply_patch_normalizer(sandbox, config),
        ),
        ToolDefinition(
            spec=_side_effect_spec(
                name="write_file",
                description=(
                    "Atomically create or replace one task-authorized UTF-8 text file. "
                    "Prefer apply_patch when changing an existing file."
                ),
                input_schema=object_schema(
                    {
                        "path": _PATH_SCHEMA,
                        "content": file_content_schema(),
                    },
                    ("path", "content"),
                ),
                risk=ToolRisk.WRITE,
                permission=ToolPermission.WRITE,
                timeout_seconds=5,
                path_arguments=("path",),
                output_limit_bytes=WRITE_OUTPUT_BYTES,
            ),
            handler=write_file_handler(sandbox, config),
            normalize_args=write_file_normalizer(sandbox, config),
        ),
    ]
    if isinstance(config.config.task.acceptance, TestRepairAcceptance):
        definitions.append(
            ToolDefinition(
                spec=_side_effect_spec(
                    name="run_test",
                    description=(
                        "Run only the task's configured acceptance test command. "
                        "The command argv, cwd, environment, timeout and limits are trusted."
                    ),
                    input_schema=object_schema(
                        {
                            "command_id": {
                                "type": "string",
                                "const": ACCEPTANCE_TEST_COMMAND_ID,
                            }
                        },
                        ("command_id",),
                    ),
                    risk=ToolRisk.EXECUTE,
                    permission=ToolPermission.EXECUTE,
                    timeout_seconds=3_635,
                    path_arguments=(),
                    output_limit_bytes=TEST_OUTPUT_BYTES + 4_096,
                ),
                handler=run_test_handler(sandbox),
            )
        )
    return tuple(sorted(definitions, key=lambda definition: definition.spec.name))


def _side_effect_spec(
    *,
    name: str,
    description: str,
    input_schema: dict,
    risk: ToolRisk,
    permission: ToolPermission,
    timeout_seconds: int,
    path_arguments: tuple[str, ...],
    output_limit_bytes: int,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema=input_schema,
        output_kind=ToolOutputKind.JSON,
        risk=risk,
        required_permissions=frozenset({permission}),
        timeout_seconds=timeout_seconds,
        retry_policy=RetryPolicy.NEVER,
        idempotent=False,
        path_arguments=path_arguments,
        output_limit_bytes=output_limit_bytes,
        approval_requirement=(
            ApprovalRequirement.REQUIRED if risk == ToolRisk.WRITE else ApprovalRequirement.NEVER
        ),
        concurrency_class=ConcurrencyClass.SERIAL,
    )


__all__ = ["register_readonly_tools", "register_side_effect_tools"]
