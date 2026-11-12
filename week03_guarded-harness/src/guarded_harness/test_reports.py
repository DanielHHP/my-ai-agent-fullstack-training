"""Bounded, deterministic parsing of pytest JUnit XML reports."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .domain import ErrorCode, TestNodeStatus
from .redaction import Redactor

MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_TEST_NODES = 100_000
MAX_FIELD_CHARS = 4_096
MAX_LABEL_CHARS = 512
MAX_FAILURE_SUMMARY_CHARS = 8 * 1024
PARSER_VERSION = 1
_REPORT_FORMATS = {
    "pytest_junit_xml": "pytest-junit-v1",
    "node_test_junit_xml": "node-test-junit-v1",
}

_UNSAFE_XML = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class TestReportError(ValueError):
    """Stable report parsing failure suitable for Completion mapping."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


@dataclass(frozen=True)
class ParsedTestCase:
    """One normalized testcase from a trusted JUnit report."""

    node_key: str
    label: str
    file: str
    status: TestNodeStatus
    failure_code: str | None = None
    failure_summary: str | None = None


@dataclass(frozen=True)
class ParsedTestReport:
    """Complete, immutable facts from one JUnit report."""

    report_digest: str
    report_size_bytes: int
    nodes: tuple[ParsedTestCase, ...]
    passed_count: int
    failed_count: int
    error_count: int
    skipped_count: int
    unknown_count: int
    failure_summary: str | None
    parser_version: int = PARSER_VERSION

    @property
    def total_count(self) -> int:
        return len(self.nodes)


@dataclass(frozen=True)
class TestNodeComparison:
    """Deterministic baseline/final testcase comparison."""

    missing_baseline_keys: tuple[str, ...]
    remaining_baseline_failures: tuple[str, ...]
    new_failed_keys: tuple[str, ...]
    new_skipped_keys: tuple[str, ...]
    unchanged_baseline_skips: tuple[str, ...]


def parse_junit_report(
    data: bytes,
    *,
    max_nodes: int = MAX_TEST_NODES,
    report_format: str = "pytest_junit_xml",
    workspace_root: str | Path | None = None,
) -> ParsedTestReport:
    """Parse one bounded pytest- or Node-compatible JUnit XML report."""

    if not isinstance(data, bytes):
        raise TypeError("report data must be bytes")
    if not data:
        raise TestReportError(ErrorCode.COMPLETION_TEST_REPORT_EMPTY, "report is empty")
    if len(data) > MAX_REPORT_BYTES:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_TOO_LARGE,
            "report exceeds the configured byte limit",
        )
    if type(max_nodes) is not int or not 1 <= max_nodes <= MAX_TEST_NODES:
        raise ValueError("max_nodes is outside the supported range")
    if report_format not in _REPORT_FORMATS:
        raise ValueError("report_format is not supported")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_UNSAFE_XML,
            "report must use UTF-8 encoding",
        )
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
            "report is not valid UTF-8 XML",
        ) from exc
    if b"\x00" in data or _UNSAFE_XML.search(data):
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_UNSAFE_XML,
            "report contains unsafe XML constructs",
        )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
            "report is not well-formed XML",
        ) from exc

    root_name = _local_name(root.tag)
    if root_name not in {"testsuites", "testsuite"}:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
            "report root must be testsuites or testsuite",
        )

    cases = tuple(root.iter())
    testcase_elements = tuple(
        element for element in cases if _local_name(element.tag) == "testcase"
    )
    if not testcase_elements:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_EMPTY,
            "report contains no test cases",
        )
    if len(testcase_elements) > max_nodes:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_TOO_LARGE,
            "report exceeds the configured test-node limit",
        )

    redactor = Redactor()
    parsed: list[ParsedTestCase] = []
    seen: set[str] = set()
    failure_texts: list[str] = []
    for element in testcase_elements:
        name = _bounded_attribute(element, "name")
        file_name = _bounded_attribute(element, "file")
        classname = _optional_attribute(element, "classname")
        if not name:
            raise TestReportError(
                ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
                "each testcase requires a name attribute",
            )
        if not file_name:
            if report_format == "node_test_junit_xml":
                raise TestReportError(
                    ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
                    "each Node testcase requires a file attribute",
                )
            file_name = _derive_test_file(classname)
        normalized_file = _normalize_report_file(
            file_name,
            report_format=report_format,
            workspace_root=workspace_root,
        )
        status, failure_code, failure_text = _classify_testcase(element)
        if failure_text is not None:
            failure_texts.append(failure_text)
        node_key = canonical_node_key(
            file=normalized_file,
            classname=classname,
            name=name,
            report_format=report_format,
        )
        if node_key in seen:
            raise TestReportError(
                ErrorCode.COMPLETION_TEST_NODE_DUPLICATE,
                "report contains duplicate normalized test nodes",
            )
        seen.add(node_key)
        label_source = f"{classname + '::' if classname else ''}{name}"
        label_result = redactor.redact(label_source).value
        label = label_result if isinstance(label_result, str) else name
        parsed.append(
            ParsedTestCase(
                node_key=node_key,
                label=label[:MAX_LABEL_CHARS],
                file=normalized_file,
                status=status,
                failure_code=failure_code or None,
                failure_summary=_redacted_summary(redactor, failure_text),
            )
        )

    _validate_suite_counts(root, parsed)
    nodes = tuple(sorted(parsed, key=lambda item: item.node_key))
    combined_failure = "\n".join(failure_texts)
    return ParsedTestReport(
        report_digest=f"sha256:{hashlib.sha256(data).hexdigest()}",
        report_size_bytes=len(data),
        nodes=nodes,
        passed_count=sum(node.status == TestNodeStatus.PASSED for node in nodes),
        failed_count=sum(node.status == TestNodeStatus.FAILED for node in nodes),
        error_count=sum(node.status == TestNodeStatus.ERROR for node in nodes),
        skipped_count=sum(
            node.status in {TestNodeStatus.SKIPPED, TestNodeStatus.XFAIL, TestNodeStatus.XPASS}
            for node in nodes
        ),
        unknown_count=sum(node.status == TestNodeStatus.UNKNOWN for node in nodes),
        failure_summary=_redacted_summary(redactor, combined_failure),
    )


def canonical_node_key(
    *,
    file: str,
    classname: str,
    name: str,
    report_format: str = "pytest_junit_xml",
) -> str:
    """Return a stable hash identity for one normalized testcase."""

    normalized_file = normalize_test_file(file)
    try:
        domain = _REPORT_FORMATS[report_format]
    except KeyError as exc:
        raise ValueError("report_format is not supported") from exc
    payload = (f"{domain}\0{normalized_file}\0{classname.strip()}\0{name.strip()}").encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _normalize_report_file(
    value: str,
    *,
    report_format: str,
    workspace_root: str | Path | None,
) -> str:
    if report_format != "node_test_junit_xml" or not Path(value).is_absolute():
        return normalize_test_file(value)
    if workspace_root is None:
        raise TestReportError(
            ErrorCode.COMPLETION_EVIDENCE_PATH_INVALID,
            "absolute Node test paths require a workspace root",
        )
    try:
        root = Path(workspace_root).resolve(strict=True)
        relative = Path(value).resolve(strict=True).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise TestReportError(
            ErrorCode.COMPLETION_EVIDENCE_PATH_INVALID,
            "Node test file must resolve inside the workspace",
        ) from exc
    return normalize_test_file(relative.as_posix())


def normalize_test_file(value: str) -> str:
    """Validate and normalize one report-relative POSIX test file path."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_FIELD_CHARS
        or "\\" in value
        or _CONTROL_CHARACTERS.search(value)
    ):
        raise TestReportError(
            ErrorCode.COMPLETION_EVIDENCE_PATH_INVALID,
            "test file path is invalid",
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise TestReportError(
            ErrorCode.COMPLETION_EVIDENCE_PATH_INVALID,
            "test file path must be normalized and workspace-relative",
        )
    return path.as_posix()


def _derive_test_file(classname: str) -> str:
    """Derive a pytest module path when xunit2 omits the file attribute."""

    parts = tuple(part for part in classname.split(".") if part)
    if not parts:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
            "testcase requires file or a module classname",
        )
    if parts[-1][:1].isupper():
        parts = parts[:-1]
    if not parts:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
            "testcase classname does not identify a module",
        )
    return normalize_test_file("/".join((*parts[:-1], f"{parts[-1]}.py")))


def compare_test_nodes(
    baseline: ParsedTestReport,
    final: ParsedTestReport,
) -> TestNodeComparison:
    """Compare normalized testcase sets and status transitions."""

    baseline_by_key = {node.node_key: node for node in baseline.nodes}
    final_by_key = {node.node_key: node for node in final.nodes}
    missing = tuple(sorted(baseline_by_key.keys() - final_by_key.keys()))
    remaining_failures: list[str] = []
    unchanged_skips: list[str] = []
    new_skips: list[str] = []
    for key, before in baseline_by_key.items():
        after = final_by_key.get(key)
        if after is None:
            continue
        if before.status in {TestNodeStatus.FAILED, TestNodeStatus.ERROR}:
            if after.status != TestNodeStatus.PASSED:
                remaining_failures.append(key)
        if before.status in {TestNodeStatus.SKIPPED, TestNodeStatus.XFAIL}:
            if after.status in {
                TestNodeStatus.SKIPPED,
                TestNodeStatus.XFAIL,
                TestNodeStatus.XPASS,
            }:
                unchanged_skips.append(key)
            elif after.status in {TestNodeStatus.UNKNOWN}:
                new_skips.append(key)
        elif after.status in {
            TestNodeStatus.SKIPPED,
            TestNodeStatus.XFAIL,
            TestNodeStatus.XPASS,
            TestNodeStatus.UNKNOWN,
        }:
            new_skips.append(key)
    new_nodes = final_by_key.keys() - baseline_by_key.keys()
    for key in new_nodes:
        if final_by_key[key].status in {
            TestNodeStatus.SKIPPED,
            TestNodeStatus.XFAIL,
            TestNodeStatus.XPASS,
            TestNodeStatus.UNKNOWN,
        }:
            new_skips.append(key)
    new_failed = tuple(
        sorted(
            key
            for key in final_by_key
            if final_by_key[key].status in {TestNodeStatus.FAILED, TestNodeStatus.ERROR}
        )
    )
    return TestNodeComparison(
        missing_baseline_keys=missing,
        remaining_baseline_failures=tuple(sorted(set(remaining_failures))),
        new_failed_keys=new_failed,
        new_skipped_keys=tuple(sorted(set(new_skips))),
        unchanged_baseline_skips=tuple(sorted(set(unchanged_skips))),
    )


def _classify_testcase(
    element: ET.Element,
) -> tuple[TestNodeStatus, str | None, str | None]:
    terminal = [
        child
        for child in element
        if _local_name(child.tag) in {"failure", "error", "skipped", "rerun"}
    ]
    if len(terminal) > 1:
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
            "testcase contains multiple terminal outcomes",
        )
    if not terminal:
        return TestNodeStatus.PASSED, None, None
    child = terminal[0]
    name = _local_name(child.tag)
    text = "".join(child.itertext()).strip()
    code = _optional_attribute(child, "type")
    if name == "failure":
        return TestNodeStatus.FAILED, code, text
    if name == "error":
        return TestNodeStatus.ERROR, code, text
    if name == "rerun":
        return TestNodeStatus.UNKNOWN, code or "rerun", text
    message = _optional_attribute(child, "message") or ""
    combined = f"{code or ''} {message} {text}".casefold()
    if "xpass" in combined:
        return TestNodeStatus.XPASS, code, text
    if "xfail" in combined:
        return TestNodeStatus.XFAIL, code, text
    return TestNodeStatus.SKIPPED, code, text


def _validate_suite_counts(
    root: ET.Element,
    nodes: list[ParsedTestCase],
) -> None:
    del nodes

    def int_attribute(element: ET.Element, name: str) -> int | None:
        value = element.attrib.get(name)
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise TestReportError(
                ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
                "suite count attribute is not an integer",
            ) from exc
        if parsed < 0:
            raise TestReportError(
                ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
                "suite count attribute is negative",
            )
        return parsed

    for suite in root.iter():
        if _local_name(suite.tag) not in {"testsuite", "testsuites"}:
            continue
        direct_cases = [child for child in suite if _local_name(child.tag) == "testcase"]
        if not direct_cases:
            continue
        observed = {
            "tests": len(direct_cases),
            "failures": sum(
                any(_local_name(child.tag) == "failure" for child in case) for case in direct_cases
            ),
            "errors": sum(
                any(_local_name(child.tag) == "error" for child in case) for case in direct_cases
            ),
            "skipped": sum(
                any(_local_name(child.tag) == "skipped" for child in case) for case in direct_cases
            ),
        }
        for key, expected in observed.items():
            declared = int_attribute(suite, key)
            if declared is not None and declared != expected:
                raise TestReportError(
                    ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
                    "suite counts do not match testcase facts",
                )


def _redacted_summary(redactor: Redactor, value: str | None) -> str | None:
    if not value:
        return None
    result = redactor.redact(value).value
    text = result if isinstance(result, str) else ""
    text = _CONTROL_CHARACTERS.sub("", text).strip()
    return text[:MAX_FAILURE_SUMMARY_CHARS] or None


def _bounded_attribute(element: ET.Element, name: str) -> str:
    value = element.attrib.get(name)
    if value is None:
        return ""
    if len(value) > MAX_FIELD_CHARS or _CONTROL_CHARACTERS.search(value):
        raise TestReportError(
            ErrorCode.COMPLETION_TEST_REPORT_MALFORMED,
            "testcase attribute exceeds the supported bounds",
        )
    return value.strip()


def _optional_attribute(element: ET.Element, name: str) -> str:
    return _bounded_attribute(element, name)


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


__all__ = [
    "MAX_FAILURE_SUMMARY_CHARS",
    "MAX_FIELD_CHARS",
    "MAX_LABEL_CHARS",
    "MAX_REPORT_BYTES",
    "MAX_TEST_NODES",
    "PARSER_VERSION",
    "ParsedTestCase",
    "ParsedTestReport",
    "TestNodeComparison",
    "TestReportError",
    "canonical_node_key",
    "compare_test_nodes",
    "normalize_test_file",
    "parse_junit_report",
]
