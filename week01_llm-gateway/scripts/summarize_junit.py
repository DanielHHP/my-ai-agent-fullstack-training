#!/usr/bin/env python3
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: summarize_junit.py <junit.xml>", file=sys.stderr)
        return 2

    junit_path = Path(sys.argv[1])
    if not junit_path.exists():
        print(f"JUnit XML not found: {junit_path}", file=sys.stderr)
        return 1

    root = ET.parse(junit_path).getroot()
    suites = root.findall("testsuite") or [root]
    tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    duration = sum(float(suite.attrib.get("time", "0")) for suite in suites)
    passed = tests - failures - errors - skipped

    test_cases = list(root.iter("testcase"))
    failed_cases: list[str] = []
    for case in test_cases:
        for child in case:
            if child.tag in {"failure", "error"}:
                classname = case.attrib.get("classname", "")
                name = case.attrib.get("name", "")
                message = child.attrib.get("message", "")
                failed_cases.append(f"- `{classname}.{name}`: {message}")

    slowest = sorted(
        test_cases,
        key=lambda case: float(case.attrib.get("time", "0")),
        reverse=True,
    )[:10]

    lines = [
        "# Acceptance Test Summary",
        "",
        f"- Total: {tests}",
        f"- Passed: {passed}",
        f"- Failed: {failures}",
        f"- Errors: {errors}",
        f"- Skipped: {skipped}",
        f"- Duration: {duration:.3f}s",
        "",
    ]

    if failed_cases:
        lines.extend(["## Failed Cases", "", *failed_cases, ""])

    if slowest:
        lines.append("## Slowest Tests")
        lines.append("")
        for case in slowest:
            lines.append(
                f"- {case.attrib.get('classname', '')}.{case.attrib.get('name', '')} "
                f"({float(case.attrib.get('time', '0')):.3f}s)"
            )
        lines.append("")

    summary_md = "\n".join(lines)
    print(summary_md)

    output_path = junit_path.parent / "summary.md"
    output_path.write_text(summary_md, encoding="utf-8")
    print(f"\nSummary written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
