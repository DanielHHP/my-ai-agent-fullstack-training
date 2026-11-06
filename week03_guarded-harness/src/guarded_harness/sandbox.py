"""Deterministic workspace, file, Git, command, and process boundaries."""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import math
import os
import posixpath
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol, runtime_checkable

from .config import ResolvedHarnessConfig, TestRepairAcceptance
from .domain import (
    CheckpointWorkspace,
    CheckpointWorkspaceEntry,
    Clock,
    ErrorCode,
    FaultInjector,
    SystemClock,
    WorkspaceIdentity,
)

MAX_IO_BYTES = 10 * 1024 * 1024
MAX_PATH_CHARS = 4096
MAX_COMMAND_ID_CHARS = 128
DEFAULT_COMMAND_TIMEOUT_SECONDS = 20.0
DEFAULT_KILL_GRACE_SECONDS = 2.0
DEFAULT_STDOUT_LIMIT_BYTES = 1024 * 1024
DEFAULT_STDERR_LIMIT_BYTES = 1024 * 1024
MAX_PATHSPECS = 1_000
MAX_DIRECTORY_SCAN_ENTRIES = 20_000
MAX_CHECKPOINT_WORKSPACE_ENTRIES = 1_000
MAX_COMMAND_TIMEOUT_SECONDS = 3600.0
MAX_KILL_GRACE_SECONDS = 30.0
_READ_CHUNK_BYTES = 64 * 1024
_PROCESS_POLL_SECONDS = 0.02

_SENSITIVE_NAMES = frozenset(
    {
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pgpass",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
    }
)
_SENSITIVE_SUFFIXES = (".jks", ".key", ".keystore", ".p12", ".pem", ".pfx")
_DEPENDENCY_SEGMENTS = frozenset(
    {
        ".nox",
        ".tox",
        ".venv",
        "__pypackages__",
        "node_modules",
        "site-packages",
        "venv",
    }
)
_TRAVERSAL_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "__pypackages__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)
_SHELL_EXECUTABLES = frozenset(
    {
        "bash",
        "busybox",
        "cmd",
        "cmd.exe",
        "dash",
        "env",
        "fish",
        "powershell",
        "pwsh",
        "sh",
        "xargs",
        "zsh",
    }
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_DISABLED_EXTERNAL_SENTINEL = b'{"external_protection":"DISABLED","schema_version":1}'


class SandboxAccess(str, Enum):
    """Filesystem operation being authorized."""

    READ = "READ"
    WRITE = "WRITE"


class SandboxWriteMode(str, Enum):
    """Atomic file-write mode."""

    CREATE = "CREATE"
    REPLACE = "REPLACE"


class ExternalProtection(str, Enum):
    """Whether Git-visible paths outside the task workspace are monitored."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class CommandEffectClass(str, Enum):
    """Effect class used to decide timeout result certainty."""

    READ_ONLY = "READ_ONLY"
    IDEMPOTENT = "IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"


class SandboxProcessStatus(str, Enum):
    """Stable process outcome observed at the Sandbox boundary."""

    START_FAILED = "START_FAILED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"


class SandboxEntryType(str, Enum):
    """Safe directory entry categories visible to downstream tools."""

    DIRECTORY = "DIRECTORY"
    FILE = "FILE"
    SYMLINK = "SYMLINK"
    OTHER = "OTHER"


class SandboxWalkLimitReason(str, Enum):
    """Stable reason explaining why a filesystem walk stopped early."""

    ENTRIES = "ENTRIES"
    FILES = "FILES"
    BYTES = "BYTES"


@runtime_checkable
class CancellationToken(Protocol):
    """Minimal cancellation source accepted by command execution."""

    def is_set(self) -> bool:
        """Return whether cancellation has been requested."""


@dataclass(frozen=True)
class SandboxViolation(ValueError):
    """Structured Sandbox rejection without sensitive values or filesystem content."""

    code: ErrorCode
    operation: str
    message: str
    path: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, ErrorCode):
            object.__setattr__(self, "code", ErrorCode(self.code))
        object.__setattr__(self, "message", self.message[:1000])
        ValueError.__init__(self, f"{self.code.value}: {self.message}")


@dataclass(frozen=True)
class SandboxPath:
    """A normalized path that passed the Sandbox policy."""

    original: str
    absolute_path: str
    workspace_relative: str
    git_relative: str
    access: SandboxAccess
    identity_digest: str
    schema_version: int = 1


@dataclass(frozen=True)
class SandboxFile:
    """A bounded, fully-read regular file."""

    path: SandboxPath
    data: bytes
    content_digest: str
    byte_count: int
    schema_version: int = 1


@dataclass(frozen=True)
class SandboxWriteResult:
    """A completed atomic write."""

    path: SandboxPath
    mode: SandboxWriteMode
    created: bool
    previous_content_digest: str | None
    content_digest: str
    byte_count: int
    schema_version: int = 1


@dataclass(frozen=True)
class SandboxDirectoryEntry:
    """One directory entry that passed Sandbox read policy."""

    name: str
    path: SandboxPath
    entry_type: SandboxEntryType
    size_bytes: int | None
    schema_version: int = 1


@dataclass(frozen=True)
class SandboxDirectoryListing:
    """Bounded and policy-filtered immediate directory listing."""

    root: SandboxPath
    entries: tuple[SandboxDirectoryEntry, ...]
    visible_count: int
    returned_count: int
    omitted_count: int
    truncated: bool
    schema_version: int = 1


@dataclass(frozen=True)
class SandboxFileWalk:
    """Bounded and policy-filtered recursive file discovery."""

    root: SandboxPath
    files: tuple[SandboxPath, ...]
    examined_entries: int
    omitted_count: int
    total_bytes: int
    truncated: bool
    limit_reason: SandboxWalkLimitReason | None
    schema_version: int = 1


@dataclass(frozen=True)
class WorkspaceEntry:
    """One Git-visible baseline entry represented only by bounded facts."""

    status: str
    path: str
    file_type: str
    content_digest: str


@dataclass(frozen=True)
class WorkspaceBaseline:
    """Immutable Git baseline for one workspace snapshot."""

    identity: WorkspaceIdentity
    head_oid_or_unborn: str
    index_digest: str
    workspace_status_digest: str
    workspace_diff_digest: str
    external_dirty_baseline_digest: str
    external_protection: ExternalProtection
    workspace_entries: tuple[WorkspaceEntry, ...]
    external_entries: tuple[WorkspaceEntry, ...]
    captured_at: datetime
    schema_version: int = 1


@dataclass(frozen=True)
class WorkspaceComparison:
    """Result of comparing a workspace against an earlier baseline."""

    equal: bool
    workspace_changed: bool
    external_changed: bool | None
    identity_changed: bool
    external_protection: ExternalProtection
    reason_codes: tuple[str, ...]
    changed_paths: tuple[str, ...]
    compared_at: datetime
    schema_version: int = 1


@dataclass(frozen=True)
class CommandPolicy:
    """Trusted process limits and executable allowlist."""

    allowed_executables: tuple[str, ...] = ("python", "git")
    default_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS
    default_kill_grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS
    default_stdout_limit_bytes: int = DEFAULT_STDOUT_LIMIT_BYTES
    default_stderr_limit_bytes: int = DEFAULT_STDERR_LIMIT_BYTES
    schema_version: int = 1

    def __post_init__(self) -> None:
        names = tuple(self.allowed_executables)
        object.__setattr__(self, "allowed_executables", names)
        if not names or any(not name or name != name.strip() for name in names):
            raise ValueError("allowed_executables must contain non-empty names")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("allowed_executables must not contain duplicates")
        if not math.isfinite(self.default_timeout_seconds) or not (
            0 < self.default_timeout_seconds <= MAX_COMMAND_TIMEOUT_SECONDS
        ):
            raise ValueError("default command timeout is out of range")
        if not math.isfinite(self.default_kill_grace_seconds) or not (
            0 <= self.default_kill_grace_seconds <= MAX_KILL_GRACE_SECONDS
        ):
            raise ValueError("default kill grace is out of range")
        for limit in (self.default_stdout_limit_bytes, self.default_stderr_limit_bytes):
            if type(limit) is not int or not 256 <= limit <= DEFAULT_STDOUT_LIMIT_BYTES:
                raise ValueError("default output limits must be between 256 and 1 MiB")


@dataclass(frozen=True)
class TrustedCommandSpec:
    """A command registered by trusted assembly code, never by the model."""

    command_id: str
    argv: tuple[str, ...]
    effect_class: CommandEffectClass
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS
    kill_grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS
    stdout_limit_bytes: int = DEFAULT_STDOUT_LIMIT_BYTES
    stderr_limit_bytes: int = DEFAULT_STDERR_LIMIT_BYTES
    schema_version: int = 2
    allow_pathspecs: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        if (
            not self.command_id
            or len(self.command_id) > MAX_COMMAND_ID_CHARS
            or not self.command_id[0].islower()
            or any(
                not (character.islower() or character.isdigit() or character in "_.-")
                for character in self.command_id
            )
        ):
            raise ValueError("command_id must use lowercase identifier characters")
        if not self.argv or any(not part or "\x00" in part for part in self.argv):
            raise ValueError("argv must contain non-empty strings without NUL")
        if not isinstance(self.effect_class, CommandEffectClass):
            raise ValueError("effect_class must be explicit")
        if type(self.allow_pathspecs) is not bool:
            raise ValueError("allow_pathspecs must be a boolean")
        if self.schema_version not in {1, 2}:
            raise ValueError("schema_version must be 1 or 2")
        if self.allow_pathspecs and self.schema_version != 2:
            raise ValueError("pathspec commands require schema_version 2")
        if self.allow_pathspecs and self.argv[-1] != "--":
            raise ValueError("pathspec commands must end their trusted argv with '--'")
        if not math.isfinite(self.timeout_seconds) or not (
            0 < self.timeout_seconds <= MAX_COMMAND_TIMEOUT_SECONDS
        ):
            raise ValueError("command timeout is out of range")
        if not math.isfinite(self.kill_grace_seconds) or not (
            0 <= self.kill_grace_seconds <= MAX_KILL_GRACE_SECONDS
        ):
            raise ValueError("kill grace is out of range")
        for limit in (self.stdout_limit_bytes, self.stderr_limit_bytes):
            if type(limit) is not int or not 256 <= limit <= DEFAULT_STDOUT_LIMIT_BYTES:
                raise ValueError("output limit must be between 256 and 1 MiB")


@dataclass(frozen=True)
class SandboxProcessResult:
    """Structured process fact returned to the Gate and downstream tools."""

    command_id: str
    status: SandboxProcessStatus
    exit_code: int | None
    signal: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    cancelled: bool
    process_group_terminated: bool
    duration_ms: int
    result_code: ErrorCode | None
    schema_version: int = 1


@dataclass(frozen=True)
class _BoundedStream:
    data: bytes
    truncated: bool
    omitted_bytes: int


class _BoundedCapture:
    """Capture head and tail while draining an entire pipe."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._prefix_limit = limit // 2
        self._tail_limit = limit - self._prefix_limit
        self._prefix = bytearray()
        self._tail: deque[bytes] = deque()
        self._tail_size = 0
        self._total = 0

    def append(self, chunk: bytes) -> None:
        self._total += len(chunk)
        if len(self._prefix) < self._prefix_limit:
            take = min(self._prefix_limit - len(self._prefix), len(chunk))
            self._prefix.extend(chunk[:take])
            chunk = chunk[take:]
        if not chunk:
            return
        self._tail.append(chunk)
        self._tail_size += len(chunk)
        while self._tail_size > self._tail_limit:
            overflow = self._tail_size - self._tail_limit
            first = self._tail[0]
            if len(first) <= overflow:
                self._tail.popleft()
                self._tail_size -= len(first)
            else:
                self._tail[0] = first[overflow:]
                self._tail_size -= overflow

    def finish(self) -> _BoundedStream:
        tail = b"".join(self._tail)
        complete = self._prefix + tail
        truncated = self._total > len(complete)
        if not truncated:
            return _BoundedStream(data=complete, truncated=False, omitted_bytes=0)
        omitted = self._total - len(self._prefix) - len(tail)
        marker = f"\n...[truncated {omitted} bytes]...\n".encode()
        return _BoundedStream(
            data=bytes(self._prefix) + marker + tail,
            truncated=True,
            omitted_bytes=omitted,
        )


class _ReaderThread(threading.Thread):
    """Drain one process pipe into a bounded capture."""

    def __init__(self, stream: object, capture: _BoundedCapture) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._capture = capture
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(_READ_CHUNK_BYTES)  # type: ignore[attr-defined]
                if not chunk:
                    return
                self._capture.append(chunk)
        except BaseException as exc:  # pragma: no cover - platform pipe failure branch
            self.error = exc


class Sandbox:
    """Single-run filesystem and process boundary."""

    def __init__(
        self,
        *,
        config: ResolvedHarnessConfig,
        workspace_root: Path,
        git_root: Path,
        git_executable: Path,
        command_policy: CommandPolicy,
        clock: Clock,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._config = config
        self._workspace_root = workspace_root
        self._git_root = git_root
        self._git_executable = git_executable
        self._workspace_prefix = (
            "." if workspace_root == git_root else workspace_root.relative_to(git_root).as_posix()
        )
        self._deny_read = config.config.task.paths.deny_read
        self._deny_write = config.config.task.paths.deny_write
        self._allow_write = config.config.task.paths.allow_write
        self._state_dir = Path(config.state_dir).resolve(strict=False)
        self._command_policy = command_policy
        self._clock = clock
        self._fault_injector = fault_injector
        self._executables = self._resolve_executables(command_policy)
        self._commands: dict[str, TrustedCommandSpec] = {}

    @classmethod
    def from_resolved_config(
        cls,
        config: ResolvedHarnessConfig,
        *,
        command_policy: CommandPolicy | None = None,
        clock: Clock | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> Sandbox:
        """Construct one Sandbox from a strictly validated resolved configuration."""

        if not _process_platform_supported():
            raise SandboxViolation(
                ErrorCode.SANDBOX_UNSUPPORTED_PLATFORM,
                "initialize",
                "POSIX process groups are required for Sandbox command execution",
            )
        workspace = Path(config.workspace_root)
        try:
            workspace_root = workspace.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_WORKSPACE_INVALID,
                "initialize",
                "workspace_root cannot be resolved",
            ) from exc
        if not workspace_root.is_dir():
            raise SandboxViolation(
                ErrorCode.SANDBOX_WORKSPACE_INVALID,
                "initialize",
                "workspace_root must be an existing directory",
            )
        git_executable = _find_executable("git")
        if git_executable is None:
            raise SandboxViolation(
                ErrorCode.SANDBOX_GIT_UNAVAILABLE,
                "initialize",
                "git executable is unavailable",
            )
        git_root = _discover_git_root(workspace_root, git_executable)
        sandbox = cls(
            config=config,
            workspace_root=workspace_root,
            git_root=git_root,
            git_executable=git_executable,
            command_policy=command_policy or CommandPolicy(),
            clock=clock or SystemClock(),
            fault_injector=fault_injector,
        )
        if isinstance(config.config.task.acceptance, TestRepairAcceptance):
            argv = config.config.task.acceptance.test_command
            sandbox.register_trusted_command(
                TrustedCommandSpec(
                    command_id="acceptance_test",
                    argv=tuple(argv),
                    effect_class=CommandEffectClass.NON_IDEMPOTENT,
                    timeout_seconds=command_policy.default_timeout_seconds
                    if command_policy
                    else DEFAULT_COMMAND_TIMEOUT_SECONDS,
                    kill_grace_seconds=command_policy.default_kill_grace_seconds
                    if command_policy
                    else DEFAULT_KILL_GRACE_SECONDS,
                    stdout_limit_bytes=command_policy.default_stdout_limit_bytes
                    if command_policy
                    else DEFAULT_STDOUT_LIMIT_BYTES,
                    stderr_limit_bytes=command_policy.default_stderr_limit_bytes
                    if command_policy
                    else DEFAULT_STDERR_LIMIT_BYTES,
                )
            )
        return sandbox

    @property
    def workspace_root(self) -> Path:
        """Return the one workspace root bound to this instance."""

        return self._workspace_root

    @property
    def git_root(self) -> Path:
        """Return the containing Git root."""

        return self._git_root

    @property
    def workspace_prefix(self) -> str:
        """Return workspace root relative to the Git root using POSIX syntax."""

        return self._workspace_prefix

    def check_read(self, path: str) -> SandboxPath:
        """Return a normalized readable path or raise a stable Sandbox rejection."""

        return self._check_path(path, SandboxAccess.READ)

    def check_write(self, path: str) -> SandboxPath:
        """Return a normalized writable path or raise a stable Sandbox rejection."""

        return self._check_path(path, SandboxAccess.WRITE)

    def revalidate_path(self, path: SandboxPath) -> SandboxPath:
        """Revalidate a previously checked path and reject identity or policy drift."""

        if not isinstance(path, SandboxPath):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "revalidate",
                "path must be a SandboxPath",
            )
        try:
            current = self._check_path(path.original, path.access)
        except SandboxViolation as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_CHANGED,
                "revalidate",
                "path no longer satisfies the original safety decision",
                path.workspace_relative,
            ) from exc
        if (
            current.absolute_path != path.absolute_path
            or current.identity_digest != path.identity_digest
        ):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_CHANGED,
                "revalidate",
                "path identity changed after it was checked",
                path.workspace_relative,
            )
        return current

    def read_file(self, path: str, *, max_bytes: int = MAX_IO_BYTES) -> SandboxFile:
        """Read one regular workspace file with a hard 10 MiB limit."""

        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_IO_BYTES:
            raise SandboxViolation(
                ErrorCode.SANDBOX_FILE_TOO_LARGE,
                "read",
                "max_bytes must be between 1 and the 10 MiB hard limit",
                path=_safe_relative_path(path),
            )
        checked = self.check_read(path)
        target = Path(checked.absolute_path)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags)
        except OSError as exc:
            code = (
                ErrorCode.SANDBOX_PATH_CHANGED
                if exc.errno in {errno.ENOENT, errno.ELOOP}
                else ErrorCode.SANDBOX_IO_ERROR
            )
            raise SandboxViolation(
                code,
                "read",
                "file cannot be opened",
                checked.workspace_relative,
            ) from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise SandboxViolation(
                    ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE,
                    "read",
                    "only regular files can be read",
                    checked.workspace_relative,
                )
            if before.st_size > max_bytes:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_FILE_TOO_LARGE,
                    "read",
                    "file exceeds the configured read limit",
                    checked.workspace_relative,
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SandboxViolation(
                        ErrorCode.SANDBOX_FILE_TOO_LARGE,
                        "read",
                        "file grew beyond the configured read limit",
                        checked.workspace_relative,
                    )
                chunks.append(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if _file_identity(before) != _file_identity(after):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_CHANGED,
                "read",
                "file changed while it was being read",
                checked.workspace_relative,
            )
        self.revalidate_path(checked)
        data = b"".join(chunks)
        return SandboxFile(
            path=checked,
            data=data,
            content_digest=_digest_bytes(data),
            byte_count=len(data),
        )

    def list_directory(
        self,
        path: str,
        *,
        max_entries: int = 500,
    ) -> SandboxDirectoryListing:
        """List one directory level through the Sandbox read boundary."""

        if type(max_entries) is not int or not 1 <= max_entries <= 500:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "list_directory",
                "max_entries must be between 1 and 500",
                _safe_relative_path(path),
            )
        root = self.check_read(path)
        target = Path(root.absolute_path)
        try:
            target_stat = os.stat(target, follow_symlinks=False)
        except OSError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_IO_ERROR,
                "list_directory",
                "directory metadata cannot be inspected",
                root.workspace_relative,
            ) from exc
        if not stat.S_ISDIR(target_stat.st_mode):
            raise SandboxViolation(
                ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE,
                "list_directory",
                "path must be a directory",
                root.workspace_relative,
            )

        names: list[str] = []
        scan_truncated = False
        try:
            with os.scandir(target) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) >= MAX_DIRECTORY_SCAN_ENTRIES:
                        scan_truncated = True
                        break
        except OSError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_IO_ERROR,
                "list_directory",
                "directory cannot be enumerated",
                root.workspace_relative,
            ) from exc

        visible: list[SandboxDirectoryEntry] = []
        omitted = 0
        for name in sorted(names):
            relative = _join_relative(root.workspace_relative, name)
            candidate = Path(target) / name
            try:
                checked = self.check_read(relative)
                is_link = candidate.is_symlink()
                target_metadata = os.stat(checked.absolute_path)
            except (SandboxViolation, OSError):
                omitted += 1
                continue
            if stat.S_ISDIR(target_metadata.st_mode):
                entry_type = SandboxEntryType.SYMLINK if is_link else SandboxEntryType.DIRECTORY
                size_bytes = None
            elif stat.S_ISREG(target_metadata.st_mode):
                entry_type = SandboxEntryType.SYMLINK if is_link else SandboxEntryType.FILE
                size_bytes = target_metadata.st_size
            else:
                omitted += 1
                continue
            visible.append(
                SandboxDirectoryEntry(
                    name=name,
                    path=checked,
                    entry_type=entry_type,
                    size_bytes=size_bytes,
                )
            )

        visible.sort(
            key=lambda entry: (
                entry.entry_type != SandboxEntryType.DIRECTORY,
                entry.name,
            )
        )
        returned = tuple(visible[:max_entries])
        truncated = scan_truncated or len(visible) > max_entries
        return SandboxDirectoryListing(
            root=root,
            entries=returned,
            visible_count=len(visible),
            returned_count=len(returned),
            omitted_count=omitted,
            truncated=truncated,
        )

    def walk_readable_files(
        self,
        path: str,
        *,
        max_files: int = 5_000,
        max_bytes: int = 64 * 1024 * 1024,
        max_entries: int = 20_000,
    ) -> SandboxFileWalk:
        """Recursively discover readable regular files without following directory links."""

        for name, value, maximum in (
            ("max_files", max_files, MAX_DIRECTORY_SCAN_ENTRIES),
            ("max_entries", max_entries, MAX_DIRECTORY_SCAN_ENTRIES),
            ("max_bytes", max_bytes, 1024 * 1024 * 1024),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_INVALID,
                    "walk",
                    f"{name} must be between 1 and {maximum}",
                    _safe_relative_path(path),
                )

        root = self.check_read(path)
        root_path = Path(root.absolute_path)
        try:
            root_metadata = os.stat(root_path)
        except OSError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_IO_ERROR,
                "walk",
                "path metadata cannot be inspected",
                root.workspace_relative,
            ) from exc

        if stat.S_ISREG(root_metadata.st_mode):
            if root_metadata.st_size > max_bytes:
                return SandboxFileWalk(
                    root=root,
                    files=(),
                    examined_entries=1,
                    omitted_count=0,
                    total_bytes=0,
                    truncated=True,
                    limit_reason=SandboxWalkLimitReason.BYTES,
                )
            return SandboxFileWalk(
                root=root,
                files=(root,),
                examined_entries=1,
                omitted_count=0,
                total_bytes=root_metadata.st_size,
                truncated=False,
                limit_reason=None,
            )
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise SandboxViolation(
                ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE,
                "walk",
                "path must be a regular file or directory",
                root.workspace_relative,
            )

        pending: deque[SandboxPath] = deque((root,))
        files: list[SandboxPath] = []
        examined = 0
        omitted = 0
        total_bytes = 0
        limit_reason: SandboxWalkLimitReason | None = None

        while pending and limit_reason is None:
            directory = pending.popleft()
            directory_path = Path(directory.absolute_path)
            try:
                with os.scandir(directory_path) as iterator:
                    entries = sorted(iterator, key=lambda item: item.name)
            except OSError:
                omitted += 1
                continue
            for entry in entries:
                if examined >= max_entries:
                    limit_reason = SandboxWalkLimitReason.ENTRIES
                    break
                examined += 1
                relative = _join_relative(directory.workspace_relative, entry.name)
                try:
                    checked = self.check_read(relative)
                    metadata = os.stat(checked.absolute_path)
                except (SandboxViolation, OSError):
                    omitted += 1
                    continue
                is_link = entry.is_symlink()
                if stat.S_ISDIR(metadata.st_mode):
                    if is_link or entry.name.casefold() in _TRAVERSAL_IGNORED_DIRECTORIES:
                        continue
                    pending.append(checked)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    omitted += 1
                    continue
                if len(files) >= max_files:
                    limit_reason = SandboxWalkLimitReason.FILES
                    break
                if total_bytes + metadata.st_size > max_bytes:
                    limit_reason = SandboxWalkLimitReason.BYTES
                    break
                files.append(checked)
                total_bytes += metadata.st_size

        return SandboxFileWalk(
            root=root,
            files=tuple(sorted(files, key=lambda item: item.workspace_relative)),
            examined_entries=examined,
            omitted_count=omitted,
            total_bytes=total_bytes,
            truncated=limit_reason is not None,
            limit_reason=limit_reason,
        )

    def write_file_atomic(
        self,
        path: str,
        data: bytes,
        *,
        mode: SandboxWriteMode,
        expected_content_digest: str | None = None,
        create_parents: bool = False,
    ) -> SandboxWriteResult:
        """Atomically create or replace one regular workspace file."""

        if not isinstance(data, bytes):
            raise SandboxViolation(
                ErrorCode.SANDBOX_IO_ERROR,
                "write",
                "data must be bytes",
                path=_safe_relative_path(path),
            )
        if len(data) > MAX_IO_BYTES:
            raise SandboxViolation(
                ErrorCode.SANDBOX_FILE_TOO_LARGE,
                "write",
                "write exceeds the 10 MiB hard limit",
                path=_safe_relative_path(path),
            )
        if not isinstance(mode, SandboxWriteMode):
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_INVALID,
                "write",
                "mode must be CREATE or REPLACE",
                path=_safe_relative_path(path),
            )
        if expected_content_digest is not None and not _valid_digest(expected_content_digest):
            raise SandboxViolation(
                ErrorCode.SANDBOX_WRITE_CONFLICT,
                "write",
                "expected_content_digest is invalid",
                path=_safe_relative_path(path),
            )
        checked = self.check_write(path)
        target = Path(checked.absolute_path)
        parent = target.parent
        created_directories: list[Path] = []
        temporary_path: Path | None = None
        committed = False
        previous_digest: str | None = None
        previous_identity: tuple[int, int, int, int] | None = None
        exists = os.path.lexists(target)
        if mode == SandboxWriteMode.CREATE and exists:
            raise SandboxViolation(
                ErrorCode.SANDBOX_WRITE_CONFLICT,
                "write",
                "CREATE requires a path that does not exist",
                checked.workspace_relative,
            )
        if mode == SandboxWriteMode.REPLACE:
            if not exists:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_WRITE_CONFLICT,
                    "write",
                    "REPLACE requires an existing regular file",
                    checked.workspace_relative,
                )
            try:
                previous_stat = os.lstat(target)
            except OSError as exc:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_IO_ERROR,
                    "write",
                    "existing file cannot be inspected",
                    checked.workspace_relative,
                ) from exc
            if not stat.S_ISREG(previous_stat.st_mode):
                raise SandboxViolation(
                    ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE,
                    "write",
                    "REPLACE only supports regular files",
                    checked.workspace_relative,
                )
            if previous_stat.st_size > MAX_IO_BYTES:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_FILE_TOO_LARGE,
                    "write",
                    "existing file exceeds the 10 MiB hard limit",
                    checked.workspace_relative,
                )
            previous_digest, previous_identity = _hash_existing_file(
                target,
                checked.workspace_relative,
            )
            if expected_content_digest is not None and previous_digest != expected_content_digest:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_WRITE_CONFLICT,
                    "write",
                    "expected content digest does not match the current file",
                    checked.workspace_relative,
                )
        try:
            if create_parents:
                created_directories = self._ensure_parent_directories(
                    parent,
                    target_relative=checked.workspace_relative,
                )
            elif not parent.exists():
                raise SandboxViolation(
                    ErrorCode.SANDBOX_IO_ERROR,
                    "write",
                    "parent directory does not exist",
                    checked.workspace_relative,
                )
            temporary_path = _create_exclusive_temp(parent, target.name)
            temporary_mode = (
                stat.S_IMODE(previous_stat.st_mode)
                if mode == SandboxWriteMode.REPLACE
                else _new_file_mode()
            )
            with os.fdopen(temporary_path[1], "wb", closefd=True) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), temporary_mode)
            temporary_path = (temporary_path[0], -1)
            self._trigger("sandbox.write.after_temp")
            rechecked = self.check_write(path)
            if rechecked.absolute_path != checked.absolute_path:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_CHANGED,
                    "write",
                    "write target resolved differently before replacement",
                    checked.workspace_relative,
                )
            if mode == SandboxWriteMode.CREATE:
                if os.path.lexists(target):
                    raise SandboxViolation(
                        ErrorCode.SANDBOX_WRITE_CONFLICT,
                        "write",
                        "target appeared before atomic creation",
                        checked.workspace_relative,
                    )
            else:
                current_stat = os.lstat(target)
                if previous_identity != _file_identity(current_stat):
                    raise SandboxViolation(
                        ErrorCode.SANDBOX_PATH_CHANGED,
                        "write",
                        "target identity changed before replacement",
                        checked.workspace_relative,
                    )
                current_digest, _ = _hash_existing_file(target, checked.workspace_relative)
                if current_digest != previous_digest:
                    raise SandboxViolation(
                        ErrorCode.SANDBOX_WRITE_CONFLICT,
                        "write",
                        "target content changed before replacement",
                        checked.workspace_relative,
                    )
            self._trigger("sandbox.write.before_replace")
            if mode == SandboxWriteMode.CREATE:
                os.link(temporary_path[0], target, follow_symlinks=False)
                try:
                    os.unlink(temporary_path[0])
                except OSError:
                    pass
            else:
                os.replace(temporary_path[0], target)
            temporary_path = None
            committed = True
            _fsync_directory(parent)
        except SandboxViolation:
            raise
        except OSError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_IO_ERROR,
                "write",
                "atomic write failed",
                checked.workspace_relative,
            ) from exc
        except BaseException:
            raise
        finally:
            if temporary_path is not None and temporary_path[1] >= 0:
                try:
                    os.close(temporary_path[1])
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path[0])
                except FileNotFoundError:
                    pass
            if not committed:
                self._cleanup_created_directories(created_directories)
        return SandboxWriteResult(
            path=checked,
            mode=mode,
            created=mode == SandboxWriteMode.CREATE,
            previous_content_digest=previous_digest,
            content_digest=_digest_bytes(data),
            byte_count=len(data),
        )

    def capture_baseline(self) -> WorkspaceBaseline:
        """Capture the configured initial workspace and optional external baseline."""

        return self._capture_baseline(enforce_clean=True)

    def capture_checkpoint_workspace(self) -> CheckpointWorkspace:
        """Capture a bounded workspace snapshot without requiring an initial clean tree."""

        baseline = self._capture_baseline(enforce_clean=False)
        return self._checkpoint_workspace_from_baseline(baseline)

    def compare_checkpoint_workspace(
        self,
        snapshot: CheckpointWorkspace,
    ) -> WorkspaceComparison:
        """Compare current workspace facts with a persisted checkpoint snapshot."""

        if not isinstance(snapshot, CheckpointWorkspace):
            raise SandboxViolation(
                ErrorCode.SANDBOX_BASELINE_INVALID,
                "compare_checkpoint_workspace",
                "snapshot must be a CheckpointWorkspace",
            )
        baseline = WorkspaceBaseline(
            identity=snapshot.identity,
            head_oid_or_unborn=snapshot.head_oid_or_unborn,
            index_digest=snapshot.index_digest,
            workspace_status_digest=snapshot.workspace_status_digest,
            workspace_diff_digest=snapshot.workspace_diff_digest,
            external_dirty_baseline_digest=snapshot.external_dirty_baseline_digest,
            external_protection=ExternalProtection(snapshot.external_protection),
            workspace_entries=tuple(
                WorkspaceEntry(
                    status=entry.status,
                    path=entry.path,
                    file_type=entry.file_type,
                    content_digest=entry.content_digest,
                )
                for entry in snapshot.workspace_entries
            ),
            external_entries=tuple(
                WorkspaceEntry(
                    status=entry.status,
                    path=entry.path,
                    file_type=entry.file_type,
                    content_digest=entry.content_digest,
                )
                for entry in snapshot.external_entries
            ),
            captured_at=snapshot.captured_at,
            schema_version=snapshot.schema_version,
        )
        return self.compare_workspace(baseline)

    def compare_workspace(self, baseline: WorkspaceBaseline) -> WorkspaceComparison:
        """Compare the current workspace with a previous baseline without replaying work."""

        if baseline.schema_version != 1:
            raise SandboxViolation(
                ErrorCode.SANDBOX_BASELINE_INVALID,
                "compare_baseline",
                "unsupported baseline schema version",
            )
        current = self._capture_baseline(enforce_clean=False)
        identity_changed = (
            baseline.identity.git_root != current.identity.git_root
            or baseline.identity.workspace_root != current.identity.workspace_root
            or baseline.identity.workspace_prefix != current.identity.workspace_prefix
            or baseline.head_oid_or_unborn != current.head_oid_or_unborn
            or baseline.index_digest != current.index_digest
        )
        workspace_changed = (
            baseline.workspace_status_digest != current.workspace_status_digest
            or baseline.workspace_diff_digest != current.workspace_diff_digest
        )
        external_changed: bool | None
        if baseline.external_protection == ExternalProtection.ENABLED:
            external_changed = (
                baseline.external_dirty_baseline_digest != current.external_dirty_baseline_digest
            )
        else:
            external_changed = None
        old_entries = {entry.path: entry for entry in baseline.workspace_entries}
        new_entries = {entry.path: entry for entry in current.workspace_entries}
        changed_paths = sorted(
            path
            for path in old_entries.keys() | new_entries.keys()
            if old_entries.get(path) != new_entries.get(path)
        )
        if external_changed:
            old_external = {entry.path: entry for entry in baseline.external_entries}
            new_external = {entry.path: entry for entry in current.external_entries}
            changed_paths.extend(
                path
                for path in old_external.keys() | new_external.keys()
                if old_external.get(path) != new_external.get(path)
            )
        reasons: list[str] = []
        if identity_changed:
            reasons.append("IDENTITY_CHANGED")
        if workspace_changed:
            reasons.append("WORKSPACE_CONTENT_CHANGED")
        if external_changed:
            reasons.append("EXTERNAL_CONTENT_CHANGED")
        return WorkspaceComparison(
            equal=not reasons,
            workspace_changed=workspace_changed,
            external_changed=external_changed,
            identity_changed=identity_changed,
            external_protection=current.external_protection,
            reason_codes=tuple(reasons),
            changed_paths=tuple(sorted(set(changed_paths)))[:1000],
            compared_at=self._clock.now(),
        )

    @staticmethod
    def _checkpoint_workspace_from_baseline(
        baseline: WorkspaceBaseline,
    ) -> CheckpointWorkspace:
        workspace_entries = baseline.workspace_entries
        external_entries = baseline.external_entries
        truncated = (
            len(workspace_entries) > MAX_CHECKPOINT_WORKSPACE_ENTRIES
            or len(external_entries) > MAX_CHECKPOINT_WORKSPACE_ENTRIES
        )
        return CheckpointWorkspace(
            identity=baseline.identity,
            head_oid_or_unborn=baseline.head_oid_or_unborn,
            index_digest=baseline.index_digest,
            workspace_status_digest=baseline.workspace_status_digest,
            workspace_diff_digest=baseline.workspace_diff_digest,
            external_dirty_baseline_digest=baseline.external_dirty_baseline_digest,
            external_protection=baseline.external_protection.value,
            workspace_entries=tuple(
                CheckpointWorkspaceEntry(
                    status=entry.status,
                    path=entry.path,
                    file_type=entry.file_type,
                    content_digest=entry.content_digest,
                )
                for entry in workspace_entries[:MAX_CHECKPOINT_WORKSPACE_ENTRIES]
            ),
            external_entries=tuple(
                CheckpointWorkspaceEntry(
                    status=entry.status,
                    path=entry.path,
                    file_type=entry.file_type,
                    content_digest=entry.content_digest,
                )
                for entry in external_entries[:MAX_CHECKPOINT_WORKSPACE_ENTRIES]
            ),
            entries_truncated=truncated,
            captured_at=baseline.captured_at,
        )

    def register_trusted_command(self, spec: TrustedCommandSpec) -> None:
        """Register a command supplied by trusted assembly code."""

        if not isinstance(spec, TrustedCommandSpec):
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_INVALID,
                "register_command",
                "command specification is invalid",
            )
        executable = spec.argv[0]
        name = Path(executable).name.casefold()
        if name in _SHELL_EXECUTABLES:
            raise SandboxViolation(
                ErrorCode.SANDBOX_SHELL_DENIED,
                "register_command",
                "shell and command-wrapper executables are not allowed",
            )
        self._resolve_executable(executable)
        existing = self._commands.get(spec.command_id)
        if existing is not None and existing != spec:
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_INVALID,
                "register_command",
                "command_id is already registered with a different specification",
            )
        self._commands[spec.command_id] = spec

    def execute(
        self,
        command_id: str,
        cancellation: CancellationToken | None = None,
        *,
        pathspecs: tuple[SandboxPath, ...] = (),
    ) -> SandboxProcessResult:
        """Execute one trusted command using a bounded POSIX process group."""

        if not _process_platform_supported():
            raise SandboxViolation(
                ErrorCode.SANDBOX_UNSUPPORTED_PLATFORM,
                "execute",
                "POSIX process groups are required",
            )
        if not isinstance(command_id, str) or not command_id:
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_UNKNOWN,
                "execute",
                "command_id must be a non-empty string",
            )
        spec = self._commands.get(command_id)
        if spec is None:
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_UNKNOWN,
                "execute",
                "command_id is not registered",
            )
        if type(pathspecs) is not tuple or len(pathspecs) > MAX_PATHSPECS:
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_INVALID,
                "execute",
                f"pathspecs must be a tuple of at most {MAX_PATHSPECS} paths",
            )
        if pathspecs and not spec.allow_pathspecs:
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_INVALID,
                "execute",
                "command does not accept pathspecs",
            )
        normalized_pathspecs: list[str] = []
        for pathspec in pathspecs:
            if not isinstance(pathspec, SandboxPath) or pathspec.access != SandboxAccess.READ:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_COMMAND_INVALID,
                    "execute",
                    "pathspecs must be SandboxPath values with READ access",
                )
            try:
                current = self.revalidate_path(pathspec)
            except SandboxViolation as exc:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_COMMAND_INVALID,
                    "execute",
                    "pathspec no longer satisfies the read boundary",
                    pathspec.workspace_relative,
                ) from exc
            normalized_pathspecs.append(current.workspace_relative)
        if len(set(normalized_pathspecs)) != len(normalized_pathspecs):
            raise SandboxViolation(
                ErrorCode.SANDBOX_COMMAND_INVALID,
                "execute",
                "pathspecs must not contain duplicates",
            )
        executable = self._resolve_executable(spec.argv[0])
        argv = (str(executable), *spec.argv[1:])
        if normalized_pathspecs:
            argv = (*argv, *normalized_pathspecs)
        with tempfile.TemporaryDirectory(prefix="guarded-harness-process-", dir="/tmp") as temp_dir:
            environment = self._minimal_environment(Path(temp_dir))
            raw = self._run_process(
                argv,
                timeout_seconds=spec.timeout_seconds,
                kill_grace_seconds=spec.kill_grace_seconds,
                stdout_limit_bytes=spec.stdout_limit_bytes,
                stderr_limit_bytes=spec.stderr_limit_bytes,
                environment=environment,
                cancellation=cancellation,
            )
        status, code = _classify_process_result(
            returncode=raw.returncode,
            timed_out=raw.timed_out,
            cancelled=raw.cancelled,
            start_failed=raw.start_failed,
            reader_failed=raw.reader_failed,
            effect_class=spec.effect_class,
            process_group_terminated=raw.process_group_terminated,
        )
        return SandboxProcessResult(
            command_id=command_id,
            status=status,
            exit_code=(
                raw.returncode if raw.returncode is not None and raw.returncode >= 0 else None
            ),
            signal=-raw.returncode if raw.returncode is not None and raw.returncode < 0 else None,
            stdout=raw.stdout.data.decode("utf-8", errors="replace"),
            stderr=raw.stderr.data.decode("utf-8", errors="replace"),
            stdout_truncated=raw.stdout.truncated,
            stderr_truncated=raw.stderr.truncated,
            timed_out=raw.timed_out,
            cancelled=raw.cancelled,
            process_group_terminated=raw.process_group_terminated,
            duration_ms=raw.duration_ms,
            result_code=code,
        )

    def _check_path(self, path: str, access: SandboxAccess) -> SandboxPath:
        lexical_relative = self._validate_path_input(path)
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._workspace_root / candidate
        candidate = Path(os.path.normpath(candidate))
        try:
            resolved = _resolve_existing_or_nearest_ancestor(candidate)
        except (OSError, RuntimeError) as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                access.value.lower(),
                "path cannot be resolved safely",
                _safe_relative_path(path),
            ) from exc
        try:
            inside_relative = resolved.relative_to(self._workspace_root)
        except ValueError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE,
                access.value.lower(),
                "path resolves outside the workspace",
                _safe_relative_path(path),
            ) from exc
        workspace_relative = "." if inside_relative == Path(".") else inside_relative.as_posix()
        try:
            git_relative_path = resolved.relative_to(self._git_root)
        except ValueError as exc:  # defensive: workspace containment was checked above
            raise SandboxViolation(
                ErrorCode.SANDBOX_IDENTITY_CHANGED,
                access.value.lower(),
                "workspace path is not contained by the configured Git root",
                workspace_relative,
            ) from exc
        git_relative = "." if git_relative_path == Path(".") else git_relative_path.as_posix()
        candidate_relatives = (workspace_relative, lexical_relative)
        if self._path_is_sensitive(candidate_relatives):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_SENSITIVE,
                access.value.lower(),
                "path is platform-sensitive",
                workspace_relative,
            )
        if access in {SandboxAccess.READ, SandboxAccess.WRITE} and self._matches_any(
            candidate_relatives,
            self._deny_read,
        ):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_DENY_READ,
                access.value.lower(),
                "path is denied for reading and writing",
                workspace_relative,
            )
        if access == SandboxAccess.WRITE:
            if self._path_is_platform_write_protected(candidate_relatives):
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_PROTECTED,
                    "write",
                    "path is protected from writes",
                    workspace_relative,
                )
            denied_by_task = self._matches_any(candidate_relatives, self._deny_write)
            allowed_by_task = self._matches_any(
                (workspace_relative,),
                self._allow_write,
            )
            if denied_by_task and not allowed_by_task:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_DENY_WRITE,
                    "write",
                    "path is denied by task write policy",
                    workspace_relative,
                )
        try:
            path_stat = os.lstat(resolved)
        except FileNotFoundError:
            path_stat = None
        except OSError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_IO_ERROR,
                access.value.lower(),
                "path metadata cannot be inspected",
                workspace_relative,
            ) from exc
        if path_stat is not None and not (
            stat.S_ISREG(path_stat.st_mode)
            or stat.S_ISDIR(path_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
        ):
            raise SandboxViolation(
                ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE,
                access.value.lower(),
                "path type is unsupported",
                workspace_relative,
            )
        identity = _path_identity_digest(
            resolved,
            workspace_relative,
            access,
            path_stat,
        )
        return SandboxPath(
            original=path,
            absolute_path=str(resolved),
            workspace_relative=workspace_relative,
            git_relative=git_relative,
            access=access,
            identity_digest=identity,
        )

    def _validate_path_input(self, path: str) -> str:
        if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "path",
                "path must be a bounded non-empty string",
            )
        if "\x00" in path or any(
            ord(character) < 32 or ord(character) == 127 for character in path
        ):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "path",
                "path contains control characters",
                _safe_relative_path(path),
            )
        if "\\" in path or ":" in path:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "path",
                "path uses unsupported separators or a Windows drive",
                _safe_relative_path(path),
            )
        windows = PureWindowsPath(path)
        if windows.is_absolute() or windows.drive:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "path",
                "Windows absolute and UNC paths are not accepted",
                _safe_relative_path(path),
            )
        parsed = PurePosixPath(path)
        if parsed.parts and parsed.parts[0].startswith("~"):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "path",
                "home-directory shorthand is not accepted",
                _safe_relative_path(path),
            )
        if _has_unsafe_filename_component(path):
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_INVALID,
                "path",
                "path contains an unstable or reserved filename component",
                _safe_relative_path(path),
            )
        if not parsed.is_absolute():
            lexical = posixpath.normpath(path.replace(os.sep, "/"))
        else:
            lexical = posixpath.normpath(
                os.path.relpath(path, self._workspace_root).replace(os.sep, "/")
            )
        return lexical

    def _path_is_sensitive(self, relatives: tuple[str, ...]) -> bool:
        for relative in relatives:
            if relative in {"", "."} or relative.startswith("../"):
                continue
            parts = [
                unicodedata.normalize("NFC", part).casefold()
                for part in PurePosixPath(relative).parts
            ]
            for index, part in enumerate(parts):
                if part == ".env" or part.startswith(".env."):
                    return True
                if part in _SENSITIVE_NAMES or part.endswith(_SENSITIVE_SUFFIXES):
                    return True
                if part == ".ssh":
                    return True
                if index + 1 < len(parts):
                    pair = (part, parts[index + 1])
                    if pair == (".aws", "credentials") or pair == (".config", "gcloud"):
                        return True
        return False

    def _path_is_platform_write_protected(self, relatives: tuple[str, ...]) -> bool:
        for relative in relatives:
            if relative in {"", "."} or relative.startswith("../"):
                continue
            parts = [part.casefold() for part in PurePosixPath(relative).parts]
            if ".git" in parts or any(part in _DEPENDENCY_SEGMENTS for part in parts):
                return True
        return False

    @staticmethod
    def _matches_any(relatives: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
        return any(_glob_match(relative, pattern) for relative in relatives for pattern in patterns)

    def _ensure_parent_directories(
        self,
        parent: Path,
        *,
        target_relative: str,
    ) -> list[Path]:
        try:
            parent.relative_to(self._workspace_root)
        except ValueError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE,
                "write",
                "parent directory resolves outside the workspace",
                target_relative,
            ) from exc
        created: list[Path] = []
        current = self._workspace_root
        relative_parent = parent.relative_to(self._workspace_root)
        for component in relative_parent.parts:
            current = current / component
            relative = current.relative_to(self._workspace_root).as_posix()
            relatives = (relative,)
            if self._path_is_sensitive(relatives):
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_SENSITIVE,
                    "write",
                    "parent directory is platform-sensitive",
                    target_relative,
                )
            if self._path_is_platform_write_protected(relatives):
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_PROTECTED,
                    "write",
                    "parent directory is protected from writes",
                    target_relative,
                )
            if self._matches_any(relatives, self._deny_read):
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_DENY_READ,
                    "write",
                    "parent directory is denied",
                    target_relative,
                )
            target_has_task_exception = self._matches_any(
                (target_relative,),
                self._allow_write,
            )
            if self._matches_any(relatives, self._deny_write) and not target_has_task_exception:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_PATH_DENY_WRITE,
                    "write",
                    "parent directory is denied by task write policy",
                    target_relative,
                )
            if os.path.lexists(current):
                value = os.lstat(current)
                if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                    raise SandboxViolation(
                        ErrorCode.SANDBOX_PATH_CHANGED,
                        "write",
                        "parent path changed type or became a symlink",
                        target_relative,
                    )
                continue
            try:
                os.mkdir(current, 0o777)
            except OSError as exc:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_IO_ERROR,
                    "write",
                    "parent directory cannot be created",
                    target_relative,
                ) from exc
            created.append(current)
        return created

    @staticmethod
    def _cleanup_created_directories(created: list[Path]) -> None:
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                return

    def _resolve_executables(self, policy: CommandPolicy) -> dict[str, Path]:
        resolved: dict[str, Path] = {}
        for name in policy.allowed_executables:
            if name == "python":
                executable = Path(sys.executable).resolve(strict=True)
            elif name == "git":
                executable = self._git_executable
            else:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_EXECUTABLE_DENIED,
                    "configure_command_policy",
                    "only python and git are fixed trusted executable names",
                )
            resolved[name] = executable
        return resolved

    def _resolve_executable(self, executable: str) -> Path:
        path = Path(executable)
        if path.name.casefold() in _SHELL_EXECUTABLES:
            raise SandboxViolation(
                ErrorCode.SANDBOX_SHELL_DENIED,
                "execute",
                "shell and command-wrapper executables are not allowed",
            )
        if path.is_absolute() or path.parent != Path("."):
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_EXECUTABLE_DENIED,
                    "execute",
                    "executable cannot be resolved",
                ) from exc
            if resolved not in set(self._executables.values()):
                raise SandboxViolation(
                    ErrorCode.SANDBOX_EXECUTABLE_DENIED,
                    "execute",
                    "executable is outside the fixed allowlist",
                )
            return resolved
        try:
            return self._executables[path.name]
        except KeyError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_EXECUTABLE_DENIED,
                "execute",
                "executable name is outside the fixed allowlist",
            ) from exc

    def _minimal_environment(self, home: Path) -> dict[str, str]:
        path_parts = [
            Path(sys.executable).parent,
            self._git_executable.parent,
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/bin"),
        ]
        path_value = os.pathsep.join(
            str(part)
            for index, part in enumerate(path_parts)
            if part.is_dir() and part not in path_parts[:index]
        )
        return {
            "PATH": path_value,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "HOME": str(home),
            "TMPDIR": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONHASHSEED": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }

    def _run_process(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        kill_grace_seconds: float,
        stdout_limit_bytes: int,
        stderr_limit_bytes: int,
        environment: dict[str, str],
        cancellation: CancellationToken | None,
    ) -> _RawProcessResult:
        started_at = time.monotonic()
        stdout_capture = _BoundedCapture(stdout_limit_bytes)
        stderr_capture = _BoundedCapture(stderr_limit_bytes)
        if cancellation is not None and cancellation.is_set():
            return _RawProcessResult(
                returncode=None,
                timed_out=False,
                cancelled=True,
                stdout=stdout_capture.finish(),
                stderr=stderr_capture.finish(),
                start_failed=False,
                reader_failed=False,
                process_group_terminated=True,
                duration_ms=_duration_ms(started_at),
            )
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=self._workspace_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            return _RawProcessResult(
                returncode=None,
                timed_out=False,
                cancelled=False,
                stdout=stdout_capture.finish(),
                stderr=stderr_capture.finish(),
                start_failed=True,
                reader_failed=False,
                process_group_terminated=False,
                duration_ms=_duration_ms(started_at),
            )
        stdout_reader = _ReaderThread(process.stdout, stdout_capture)
        stderr_reader = _ReaderThread(process.stderr, stderr_capture)
        stdout_reader.start()
        stderr_reader.start()
        timed_out = False
        cancelled = False
        deadline = started_at + timeout_seconds
        while process.poll() is None:
            if cancellation is not None and cancellation.is_set():
                cancelled = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                process.wait(timeout=min(_PROCESS_POLL_SECONDS, remaining))
            except subprocess.TimeoutExpired:
                continue
        process_group_terminated = True
        if process.poll() is None:
            process_group_terminated = _terminate_process_group(
                process.pid,
                process,
                kill_grace_seconds,
            )
        elif _process_group_exists(process.pid):
            process_group_terminated = _terminate_process_group(
                process.pid,
                process,
                kill_grace_seconds,
            )
        stdout_reader.join(timeout=max(1.0, kill_grace_seconds + 0.5))
        stderr_reader.join(timeout=max(1.0, kill_grace_seconds + 0.5))
        if stdout_reader.is_alive() or stderr_reader.is_alive():
            process_group_terminated = False
            if not _terminate_process_group(process.pid, process, kill_grace_seconds):
                process_group_terminated = False
            stdout_reader.join(timeout=0.5)
            stderr_reader.join(timeout=0.5)
        reader_failed = bool(stdout_reader.error or stderr_reader.error)
        return _RawProcessResult(
            returncode=process.returncode,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout=stdout_capture.finish(),
            stderr=stderr_capture.finish(),
            start_failed=False,
            reader_failed=reader_failed or stdout_reader.is_alive() or stderr_reader.is_alive(),
            process_group_terminated=process_group_terminated,
            duration_ms=_duration_ms(started_at),
        )

    def _capture_baseline(self, *, enforce_clean: bool) -> WorkspaceBaseline:
        require_clean = self._config.config.workspace.require_clean_workspace
        protect_external = self._config.config.workspace.protect_other_git_changes
        head = self._git_head()
        index_output = self._run_git(
            (
                "ls-files",
                "--stage",
                "-z",
            )
        )
        index_digest = _digest_bytes(index_output)
        index_modes = _parse_index_modes(index_output)
        status_output = self._run_git(
            (
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
            )
        )
        status_items = _parse_git_status(status_output)
        workspace_items: list[tuple[str, str]] = []
        external_items: list[tuple[str, str]] = []
        for code, path in status_items:
            if self._git_path_is_state_dir(path):
                continue
            if self._git_path_in_workspace(path):
                workspace_items.append((code, path))
            elif protect_external:
                external_items.append((code, path))
        if enforce_clean and require_clean and workspace_items:
            raise SandboxViolation(
                ErrorCode.SANDBOX_WORKSPACE_DIRTY,
                "capture_baseline",
                "workspace contains initial Git-visible changes",
            )
        workspace_entries = tuple(
            self._workspace_entry(code, path, index_modes)
            for code, path in sorted(workspace_items, key=lambda item: item[1])
        )
        external_entries = tuple(
            self._workspace_entry(code, path, index_modes)
            for code, path in sorted(external_items, key=lambda item: item[1])
        )
        workspace_status_digest = _digest_json([entry.__dict__ for entry in workspace_entries])
        workspace_diff_digest = self._workspace_diff_digest(head)
        if protect_external:
            external_protection = ExternalProtection.ENABLED
            external_digest = _digest_json([entry.__dict__ for entry in external_entries])
        else:
            external_protection = ExternalProtection.DISABLED
            external_digest = _digest_bytes(_DISABLED_EXTERNAL_SENTINEL)
        baseline_payload = {
            "git_root": str(self._git_root),
            "workspace_root": str(self._workspace_root),
            "workspace_prefix": self._workspace_prefix,
            "head": head,
            "index_digest": index_digest,
            "workspace_status_digest": workspace_status_digest,
            "workspace_diff_digest": workspace_diff_digest,
            "external_dirty_baseline_digest": external_digest,
            "external_protection": external_protection.value,
            "schema_version": 1,
        }
        baseline_fingerprint = _digest_json(baseline_payload)
        identity = WorkspaceIdentity(
            git_root=str(self._git_root),
            workspace_root=str(self._workspace_root),
            workspace_prefix=self._workspace_prefix,
            baseline_fingerprint=baseline_fingerprint,
            external_dirty_baseline_fingerprint=external_digest,
        )
        return WorkspaceBaseline(
            identity=identity,
            head_oid_or_unborn=head,
            index_digest=index_digest,
            workspace_status_digest=workspace_status_digest,
            workspace_diff_digest=workspace_diff_digest,
            external_dirty_baseline_digest=external_digest,
            external_protection=external_protection,
            workspace_entries=workspace_entries,
            external_entries=external_entries,
            captured_at=self._clock.now(),
        )

    def _workspace_entry(
        self,
        status_code: str,
        repository_relative: str,
        index_modes: dict[str, str],
    ) -> WorkspaceEntry:
        absolute = self._git_root / repository_relative
        mode = index_modes.get(repository_relative)
        if mode == "160000":
            object_id = self._gitlink_object(repository_relative)
            return WorkspaceEntry(
                status=status_code,
                path=repository_relative,
                file_type="gitlink",
                content_digest=_digest_bytes(f"gitlink:{object_id}".encode()),
            )
        try:
            file_stat = os.lstat(absolute)
        except FileNotFoundError:
            return WorkspaceEntry(
                status=status_code,
                path=repository_relative,
                file_type="missing",
                content_digest=_digest_bytes(b"missing"),
            )
        except OSError as exc:
            raise SandboxViolation(
                ErrorCode.SANDBOX_BASELINE_INVALID,
                "capture_baseline",
                "Git-visible path cannot be inspected",
                repository_relative,
            ) from exc
        if stat.S_ISLNK(file_stat.st_mode):
            try:
                target = os.readlink(absolute)
            except OSError as exc:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_BASELINE_INVALID,
                    "capture_baseline",
                    "symlink target cannot be read",
                    repository_relative,
                ) from exc
            return WorkspaceEntry(
                status=status_code,
                path=repository_relative,
                file_type="symlink",
                content_digest=_digest_bytes(b"symlink\0" + os.fsencode(target)),
            )
        if stat.S_ISREG(file_stat.st_mode):
            return WorkspaceEntry(
                status=status_code,
                path=repository_relative,
                file_type="file",
                content_digest=_hash_file(absolute, repository_relative),
            )
        raise SandboxViolation(
            ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE,
            "capture_baseline",
            "Git-visible directory or special file type is unsupported",
            repository_relative,
        )

    def _git_head(self) -> str:
        proc = self._run_git(("rev-parse", "--verify", "HEAD"), allow_failure=True)
        if proc:
            return proc.decode("ascii", errors="strict").strip()
        self._run_git(("rev-parse", "--is-inside-work-tree"))
        return "UNBORN"

    def _workspace_diff_digest(self, head: str) -> str:
        args = ["diff", "--no-ext-diff", "--no-textconv", "--binary", "--no-color"]
        if head != "UNBORN":
            args.append("HEAD")
        path_arg = "." if self._workspace_prefix == "." else self._workspace_prefix
        args.extend(("--", path_arg))
        diff = self._run_git(tuple(args))
        return _digest_bytes(diff)

    def _run_git(self, args: tuple[str, ...], *, allow_failure: bool = False) -> bytes:
        environment = self._git_environment()
        raw = self._run_process(
            (
                str(self._git_executable),
                "-C",
                str(self._git_root),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *args,
            ),
            timeout_seconds=30.0,
            kill_grace_seconds=1.0,
            stdout_limit_bytes=4 * 1024 * 1024,
            stderr_limit_bytes=4 * 1024 * 1024,
            environment=environment,
            cancellation=None,
        )
        if raw.start_failed:
            raise SandboxViolation(
                ErrorCode.SANDBOX_GIT_UNAVAILABLE,
                "git",
                "Git process could not be started",
            )
        if raw.timed_out or raw.cancelled or raw.reader_failed:
            raise SandboxViolation(
                ErrorCode.SANDBOX_BASELINE_INVALID,
                "git",
                "Git command did not return a complete result",
            )
        if raw.returncode != 0:
            if allow_failure:
                return b""
            raise SandboxViolation(
                ErrorCode.SANDBOX_BASELINE_INVALID,
                "git",
                "Git command failed",
            )
        if raw.stdout.truncated or raw.stderr.truncated:
            raise SandboxViolation(
                ErrorCode.SANDBOX_BASELINE_INVALID,
                "git",
                "Git output exceeded the baseline capture limit",
            )
        return raw.stdout.data

    def _git_environment(self) -> dict[str, str]:
        return {
            "PATH": os.pathsep.join(
                str(path)
                for path in (self._git_executable.parent, Path("/usr/bin"), Path("/bin"))
                if path.is_dir()
            ),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }

    def _git_path_in_workspace(self, repository_relative: str) -> bool:
        if self._workspace_prefix == ".":
            return True
        return repository_relative == self._workspace_prefix or repository_relative.startswith(
            f"{self._workspace_prefix}/"
        )

    def _git_path_is_state_dir(self, repository_relative: str) -> bool:
        try:
            state_relative = self._state_dir.relative_to(self._git_root).as_posix()
        except ValueError:
            return False
        return repository_relative == state_relative or repository_relative.startswith(
            f"{state_relative}/"
        )

    def _gitlink_object(self, repository_relative: str) -> str:
        output = self._run_git(("ls-files", "--stage", "-z", "--", repository_relative))
        for record in output.split(b"\x00"):
            if not record:
                continue
            header, separator, _ = record.partition(b"\t")
            if not separator:
                continue
            parts = header.split()
            if len(parts) >= 2:
                return parts[1].decode("ascii", errors="strict")
        raise SandboxViolation(
            ErrorCode.SANDBOX_BASELINE_INVALID,
            "capture_baseline",
            "gitlink object ID is unavailable",
            repository_relative,
        )

    def _trigger(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector.trigger(point)


@dataclass(frozen=True)
class _RawProcessResult:
    returncode: int | None
    timed_out: bool
    cancelled: bool
    stdout: _BoundedStream
    stderr: _BoundedStream
    start_failed: bool
    reader_failed: bool
    process_group_terminated: bool
    duration_ms: int


def _process_platform_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "killpg")
        and (sys.platform.startswith("linux") or sys.platform == "darwin")
    )


def _find_executable(name: str) -> Path | None:
    search_path = os.pathsep.join(("/usr/local/bin", "/usr/bin", "/bin"))
    found = shutil.which(name, path=search_path)
    return Path(found).resolve(strict=True) if found else None


def _discover_git_root(workspace_root: Path, git_executable: Path) -> Path:
    environment = {
        "PATH": os.pathsep.join((str(git_executable.parent), "/usr/bin", "/bin")),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            [str(git_executable), "-C", str(workspace_root), "rev-parse", "--show-toplevel"],
            cwd=workspace_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxViolation(
            ErrorCode.SANDBOX_GIT_ROOT_INVALID,
            "initialize",
            "Git root could not be determined",
        ) from exc
    if result.returncode != 0:
        raise SandboxViolation(
            ErrorCode.SANDBOX_GIT_ROOT_INVALID,
            "initialize",
            "workspace must be inside a Git worktree",
        )
    try:
        git_root = Path(os.fsdecode(result.stdout.rstrip(b"\r\n"))).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise SandboxViolation(
            ErrorCode.SANDBOX_GIT_ROOT_INVALID,
            "initialize",
            "Git root path is invalid",
        ) from exc
    if not git_root.is_dir():
        raise SandboxViolation(
            ErrorCode.SANDBOX_GIT_ROOT_INVALID,
            "initialize",
            "Git root must be a directory",
        )
    try:
        workspace_root.relative_to(git_root)
    except ValueError as exc:
        raise SandboxViolation(
            ErrorCode.SANDBOX_GIT_ROOT_INVALID,
            "initialize",
            "workspace root is outside the reported Git root",
        ) from exc
    return git_root


def _resolve_existing_or_nearest_ancestor(candidate: Path) -> Path:
    if candidate.exists() or candidate.is_symlink():
        return candidate.resolve(strict=True)
    cursor = candidate
    tail: list[str] = []
    while not cursor.exists():
        if cursor.is_symlink():
            raise FileNotFoundError("broken symlink in path")
        if cursor.parent == cursor:
            raise FileNotFoundError("no existing ancestor")
        tail.append(cursor.name)
        cursor = cursor.parent
    resolved_ancestor = cursor.resolve(strict=True)
    return resolved_ancestor.joinpath(*reversed(tail))


def _path_identity_digest(
    resolved: Path,
    relative: str,
    access: SandboxAccess,
    file_stat: os.stat_result | None,
) -> str:
    payload: dict[str, Any] = {
        "schema": "sandbox-path-identity-v1",
        "absolute_path": str(resolved),
        "workspace_relative": relative,
        "access": access.value,
    }
    if file_stat is None:
        payload["type"] = "missing"
    else:
        payload.update(
            {
                "device": file_stat.st_dev,
                "inode": file_stat.st_ino,
                "mode": file_stat.st_mode,
                "size": file_stat.st_size,
                "mtime_ns": file_stat.st_mtime_ns,
            }
        )
    return _digest_json(payload)


def _file_identity(file_stat: os.stat_result) -> tuple[int, int, int, int]:
    return (file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns)


def _hash_existing_file(path: Path, relative: str) -> tuple[str, tuple[int, int, int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SandboxViolation(
            ErrorCode.SANDBOX_IO_ERROR,
            "write",
            "existing file cannot be opened safely",
            relative,
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_IO_BYTES:
            raise SandboxViolation(
                ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE,
                "write",
                "existing target must be a bounded regular file",
                relative,
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _file_identity(before) != _file_identity(after):
        raise SandboxViolation(
            ErrorCode.SANDBOX_PATH_CHANGED,
            "write",
            "existing file changed while being inspected",
            relative,
        )
    return f"sha256:{digest.hexdigest()}", _file_identity(after)


def _create_exclusive_temp(parent: Path, target_name: str) -> tuple[Path, int]:
    for _ in range(20):
        candidate = parent / f".{target_name}.{secrets.token_hex(8)}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(candidate, flags, 0o600)
        except FileExistsError:
            continue
        return candidate, descriptor
    raise OSError(errno.EEXIST, "cannot allocate an exclusive temporary file")


def _new_file_mode() -> int:
    old_umask = os.umask(0)
    os.umask(old_umask)
    return 0o666 & ~old_umask


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _hash_file(path: Path, relative: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SandboxViolation(
            ErrorCode.SANDBOX_BASELINE_INVALID,
            "capture_baseline",
            "regular file cannot be hashed",
            relative,
        ) from exc
    try:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                return f"sha256:{digest.hexdigest()}"
            digest.update(chunk)
    finally:
        os.close(descriptor)


def _parse_index_modes(output: bytes) -> dict[str, str]:
    modes: dict[str, str] = {}
    for record in output.split(b"\x00"):
        if not record:
            continue
        header, separator, path_bytes = record.partition(b"\t")
        if not separator:
            continue
        parts = header.split()
        if len(parts) < 2:
            continue
        modes[os.fsdecode(bytes(path_bytes))] = parts[0].decode("ascii", errors="strict")
    return modes


def _parse_git_status(output: bytes) -> list[tuple[str, str]]:
    records = output.split(b"\x00")
    items: list[tuple[str, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        marker = record[:1]
        if marker == b"1":
            parts = record.split(b" ", 8)
            if len(parts) != 9:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_BASELINE_INVALID,
                    "capture_baseline",
                    "Git status ordinary record is malformed",
                )
            items.append((parts[1].decode("ascii"), os.fsdecode(bytes(parts[8]))))
        elif marker == b"2":
            parts = record.split(b" ", 9)
            if len(parts) != 10:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_BASELINE_INVALID,
                    "capture_baseline",
                    "Git status rename record is malformed",
                )
            items.append((parts[1].decode("ascii"), os.fsdecode(bytes(parts[9]))))
            if index < len(records):
                index += 1
        elif marker == b"u":
            parts = record.split(b" ", 10)
            if len(parts) != 11:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_BASELINE_INVALID,
                    "capture_baseline",
                    "Git status unmerged record is malformed",
                )
            items.append((parts[1].decode("ascii"), os.fsdecode(bytes(parts[10]))))
        elif marker == b"?":
            items.append(("??", os.fsdecode(bytes(record[2:]))))
        elif marker == b"!":
            continue
        elif marker == b"#":
            continue
        else:
            raise SandboxViolation(
                ErrorCode.SANDBOX_BASELINE_INVALID,
                "capture_baseline",
                "Git status contains an unsupported record",
            )
    return items


def _glob_match(relative: str, pattern: str) -> bool:
    if relative in {"", "."}:
        return pattern in {"", "."}
    path_parts = tuple(PurePosixPath(relative).parts)
    pattern_parts = tuple(PurePosixPath(pattern).parts)

    def match(path_index: int, pattern_index: int) -> bool:
        while pattern_index < len(pattern_parts):
            current = pattern_parts[pattern_index]
            if current == "**":
                if pattern_index + 1 == len(pattern_parts):
                    return True
                return any(
                    match(path_index + offset, pattern_index + 1)
                    for offset in range(len(path_parts) - path_index + 1)
                )
            if path_index >= len(path_parts):
                return False
            if not fnmatch.fnmatchcase(path_parts[path_index], current):
                return False
            path_index += 1
            pattern_index += 1
        return path_index == len(path_parts)

    return match(0, 0)


def _has_unsafe_filename_component(path: str) -> bool:
    for part in PurePosixPath(path).parts:
        if part in {"/", ".", ".."}:
            continue
        if part.endswith((".", " ")):
            return True
        base_name = part.rstrip(" .").split(".", 1)[0].casefold()
        if base_name in _WINDOWS_RESERVED_NAMES:
            return True
    return False


def _digest_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _join_relative(parent: str, name: str) -> str:
    if parent == ".":
        return name
    return f"{parent}/{name}"


def _digest_json(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _digest_bytes(payload)


def _valid_digest(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    suffix = value[7:]
    return len(suffix) == 64 and all(character in "0123456789abcdef" for character in suffix)


def _safe_relative_path(path: object) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    return path[:1024]


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(
    process_group_id: int,
    process: subprocess.Popen[bytes],
    grace_seconds: float,
) -> bool:
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    grace_deadline = time.monotonic() + grace_seconds
    while time.monotonic() < grace_deadline:
        try:
            process.wait(
                timeout=min(
                    _PROCESS_POLL_SECONDS,
                    max(0.0, grace_deadline - time.monotonic()),
                )
            )
        except subprocess.TimeoutExpired:
            pass
        if not _process_group_exists(process_group_id):
            return True
        time.sleep(0.01)
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    try:
        process.wait(timeout=max(0.1, min(1.0, grace_seconds)))
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group_id):
            return True
        time.sleep(0.01)
    return False


def _classify_process_result(
    *,
    returncode: int | None,
    timed_out: bool,
    cancelled: bool,
    start_failed: bool,
    reader_failed: bool,
    effect_class: CommandEffectClass,
    process_group_terminated: bool,
) -> tuple[SandboxProcessStatus, ErrorCode | None]:
    if start_failed:
        return SandboxProcessStatus.START_FAILED, ErrorCode.SANDBOX_COMMAND_START_FAILED
    if reader_failed or not process_group_terminated:
        return SandboxProcessStatus.UNKNOWN_RESULT, ErrorCode.SANDBOX_RESULT_UNKNOWN
    if cancelled:
        if effect_class == CommandEffectClass.NON_IDEMPOTENT:
            return SandboxProcessStatus.UNKNOWN_RESULT, ErrorCode.SANDBOX_COMMAND_CANCELLED
        return SandboxProcessStatus.CANCELLED, ErrorCode.SANDBOX_COMMAND_CANCELLED
    if timed_out:
        if effect_class == CommandEffectClass.NON_IDEMPOTENT:
            return SandboxProcessStatus.UNKNOWN_RESULT, ErrorCode.SANDBOX_COMMAND_TIMEOUT
        return SandboxProcessStatus.TIMED_OUT, ErrorCode.SANDBOX_COMMAND_TIMEOUT
    if returncode == 0:
        return SandboxProcessStatus.SUCCEEDED, None
    return SandboxProcessStatus.FAILED, None


__all__ = [
    "CancellationToken",
    "CommandEffectClass",
    "CommandPolicy",
    "ExternalProtection",
    "Sandbox",
    "SandboxAccess",
    "SandboxFile",
    "SandboxPath",
    "SandboxProcessResult",
    "SandboxProcessStatus",
    "SandboxViolation",
    "SandboxWriteMode",
    "SandboxWriteResult",
    "TrustedCommandSpec",
    "WorkspaceBaseline",
    "WorkspaceComparison",
    "WorkspaceEntry",
]
