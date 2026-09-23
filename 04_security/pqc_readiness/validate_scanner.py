"""Scanner validation mode: measure precision and recall on YOUR files before trusting it.

The scanner was graded only against a synthetic sample its author planted. Anyone
adapting it should measure it on their own code first. This module compares a scan with
a CSV of findings a person has marked by hand, and reports:

    precision = findings that were expected / all findings   (how much output is real)
    recall    = expected rows that were found / all rows     (how much real crypto it saw)

EXPECTED-FINDINGS CSV FORMAT (see sample_expected_findings.csv)
--------------------------------------------------------------
    file      path relative to the scanned folder, forward slashes
    line      line number (1-based)
    category  quantum-vulnerable | classically weak | PQC-ready
    rule_id   OPTIONAL. Leave blank to grade by category only, which is the natural
              choice when marking your own files without learning this tool's rule ids.

Two ways to mark a line, and how each is matched:
  * WITH a rule_id: one row per rule; matches exactly one finding with that rule.
  * WITHOUT a rule_id: one row per (file, line, category); it covers EVERY finding of that
    category on that line. A TLS cipher-suite line can trip three quantum-vulnerable rules
    at once, and a person marking it should not have to know that.
Rows that name a rule are matched first, so a category-only row cannot take a finding a
more specific row needs. Duplicate rows are rejected as a key error.

WHERE SHOULD THE EXPECTED ROWS COME FROM?
-----------------------------------------
From a person reading the files, or from an independent inventory. Never from this
scanner's own output: a key copied from the scanner can only ever agree with it.

Usage (read-only; the folder is hashed before and after to prove nothing changed):
    py validate_scanner.py <folder> <expected.csv>
"""

from __future__ import annotations

import csv
import io
import sys
from dataclasses import dataclass
from pathlib import Path

from common import HERE, SAMPLE_CODEBASE, Check, section
from crypto_scanner import CW, PQ, QV, Finding, ScanResult, scan, snapshot, snapshot_diff
from sample_manifest import resolve

SAMPLE_EXPECTED_CSV = HERE / "sample_expected_findings.csv"
CATEGORIES = (QV, CW, PQ)
REQUIRED = ("file", "line", "category")


class ValidationError(ValueError):
    """The expected-findings CSV is malformed."""


@dataclass(frozen=True, order=True)
class Expected:
    path: str
    line: int
    category: str
    rule_id: str                    # "" when grading by category only


@dataclass(frozen=True)
class Metrics:
    matched_rows: int               # expected rows satisfied    -> recall numerator
    matched_findings: int           # findings that were expected -> precision numerator
    findings: int
    expected: int
    missed: tuple[Expected, ...]
    extra: tuple[tuple[str, int, str], ...]      # (file, line, rule id) not expected

    @property
    def precision(self) -> float | None:
        """None, not 1.0, when there are no findings: nothing was measured."""
        return self.matched_findings / self.findings if self.findings else None

    @property
    def recall(self) -> float | None:
        """None, not 1.0, when nothing was expected: nothing was measured."""
        return self.matched_rows / self.expected if self.expected else None


def _cell(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()          # short rows arrive as None


def parse_expected(text: str) -> list[Expected]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValidationError(f"expected-findings CSV: missing column(s) {missing}")
    rows: list[Expected] = []
    for n, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue                             # blank line
        line, category = _cell(row, "line"), _cell(row, "category")
        if not line.isdigit() or int(line) < 1:
            raise ValidationError(f"row {n}: line must be a positive whole number")
        if category not in CATEGORIES:
            raise ValidationError(f"row {n}: category must be one of {CATEGORIES}")
        if not _cell(row, "file"):
            raise ValidationError(f"row {n}: file is required")
        rows.append(Expected(_cell(row, "file"), int(line), category, _cell(row, "rule_id")))
    if len(set(rows)) != len(rows):
        raise ValidationError("expected-findings CSV: duplicate rows")
    return rows


def _same_place(f: Finding, e: Expected) -> bool:
    return f.path == e.path and f.line == e.line and f.category == e.category


def evaluate(result: ScanResult, expected: list[Expected]) -> Metrics:
    unused = list(result.findings)
    missed = []
    # Specific rows first, so a category-only row cannot take a finding they need.
    for e in sorted(expected, key=lambda e: not e.rule_id):
        if e.rule_id:
            hits = [next((f for f in unused if _same_place(f, e) and f.rule_id == e.rule_id),
                         None)]
            hits = [h for h in hits if h is not None]
        else:
            hits = [f for f in unused if _same_place(f, e)]
        if not hits:
            missed.append(e)
        for h in hits:
            unused.remove(h)
    matched_findings = len(result.findings) - len(unused)
    return Metrics(len(expected) - len(missed), matched_findings, len(result.findings),
                   len(expected), tuple(sorted(missed)),
                   tuple((f.path, f.line, f.rule_id) for f in unused))


def _pct(value: float | None) -> str:
    return "n/a (nothing to measure)" if value is None else f"{value:.1%}"


def print_metrics(m: Metrics) -> None:
    print(f"  precision {_pct(m.precision)}  ({m.matched_findings} of {m.findings} findings "
          "were expected)")
    print(f"  recall    {_pct(m.recall)}  ({m.matched_rows} of {m.expected} expected rows "
          "were found)")
    for e in m.missed:
        print(f"    MISSED   {e.path}:{e.line} {e.category} {e.rule_id}")
    for path, line, rule in m.extra:
        print(f"    EXTRA    {path}:{line} {rule}")


def validate(folder: Path, expected_csv: Path) -> tuple[Metrics, bool]:
    """Scan folder read-only and grade it. Returns (metrics, files untouched)."""
    expected = parse_expected(expected_csv.read_text(encoding="utf-8-sig"))
    before = snapshot(folder)
    result = scan(folder)
    return evaluate(result, expected), not snapshot_diff(before, snapshot(folder))


# ======================================================================================
# SELF-CHECKS (run by run_all.py)
# ======================================================================================
def _csv(rows: list[Expected], with_rule: bool = True) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["file", "line", "category", "rule_id"])
    for e in rows:
        writer.writerow([e.path, e.line, e.category, e.rule_id if with_rule else ""])
    return out.getvalue()


def _manifest_rows() -> list[Expected]:
    rule_class = {"QV": QV, "CW": CW, "PQ": PQ}
    return sorted(Expected(f, line, rule_class[rule[:2]], rule)
                  for f, line, rule in resolve(SAMPLE_CODEBASE))


def _rejects(text: str) -> bool:
    try:
        parse_expected(text)
    except ValidationError:
        return True
    return False


def _scoring_checks(result: ScanResult, manifest: list[Expected]) -> list[Check]:
    # Degraded key, answer worked out by hand from the 41 true rows:
    #   drop 2 rows (single-finding lines)   -> those 2 findings become EXTRA
    #   mislabel 1 row's category            -> the row is MISSED, its finding EXTRA
    #   add 1 row on a line with no crypto   -> MISSED
    # rows = 41 - 2 + 1 = 40, matched rows = 38; findings 41, matched findings = 38
    # missed = 2, extra = 3, precision = 38/41, recall = 38/40.
    wrong = Expected(manifest[2].path, manifest[2].line,
                     CW if manifest[2].category != CW else QV, "")
    degraded = [wrong] + manifest[3:] + [Expected("payments_api/tls_client.py", 1, QV, "")]
    worse = evaluate(result, degraded)
    # The natural hand-marked key: one category-only row per (file, line, category).
    natural = sorted({Expected(e.path, e.line, e.category, "") for e in manifest})
    by_category = evaluate(result, natural)
    # nginx line 7 trips QV-RSA, QV-ECC and QV-DH. A category-only row listed BEFORE a
    # QV-RSA row must not starve it: both rows match (worked by hand).
    suite = [Expected("config/gateway_nginx.conf", 7, QV, ""),
             Expected("config/gateway_nginx.conf", 7, QV, "QV-RSA")]
    order = evaluate(result, suite)
    wrong_rule = evaluate(result, [Expected("config/gateway_nginx.conf", 7, QV, "QV-DSA")])
    return [
        Check("Validate: a degraded key scores the hand-computed 38/41 and 38/40",
              (worse.matched_findings, worse.findings, worse.matched_rows, worse.expected)
              == (38, 41, 38, 40) and len(worse.missed) == 2 and len(worse.extra) == 3,
              f"precision {_pct(worse.precision)}, recall {_pct(worse.recall)}"),
        Check("Validate: the natural category-only key (one row per line and class) "
              "scores 100% / 100%", by_category.precision == 1.0 and by_category.recall == 1.0,
              f"{len(natural)} rows; precision {_pct(by_category.precision)}"),
        Check("Validate: rule-named rows are matched before category-only rows",
              order.matched_rows == 2 and order.matched_findings == 3),
        Check("Validate: a row naming the wrong rule on a real finding is MISSED",
              wrong_rule.matched_rows == 0 and len(wrong_rule.missed) == 1),
    ]


def _parsing_checks() -> list[Check]:
    header = "file,line,category,rule_id\n"
    row = "config/gateway_nginx.conf,7,quantum-vulnerable,QV-RSA\n"
    bad = {"missing line column": "file,category\nx.py,PQC-ready\n",
           "unknown category": header + "x.py,3,quantum-proof,\n",
           "line not a number": header + "x.py,three,PQC-ready,\n",
           "duplicate row": header + row + row}
    accepted = [label for label, text in bad.items() if not _rejects(text)]
    good = {"byte-order mark": "﻿" + header + row,
            "short row without rule_id": header + "x.py,3,PQC-ready\n",
            "blank line": header + row + ",,,\n"}
    refused = [label for label, text in good.items() if _rejects(text)]
    return [Check("Validate: malformed expected-findings rows are rejected",
                  not accepted, f"accepted {accepted}" if accepted else f"{len(bad)}/{len(bad)}"),
            Check("Validate: Excel-style CSVs (BOM, short row, blank line) are accepted",
                  not refused, f"refused {refused}" if refused else f"{len(good)}/{len(good)}")]


def validation_checks() -> list[Check]:
    shipped = parse_expected(SAMPLE_EXPECTED_CSV.read_text(encoding="utf-8-sig"))
    manifest = _manifest_rows()
    result = scan(SAMPLE_CODEBASE)
    perfect, untouched = validate(SAMPLE_CODEBASE, SAMPLE_EXPECTED_CSV)
    empty = evaluate(result, [])
    return [
        Check("Validate: sample_expected_findings.csv matches the planted answer key",
              sorted(shipped) == manifest, f"{len(shipped)} rows"),
        Check("Validate: sample scores precision 100% and recall 100%, files untouched",
              perfect.precision == 1.0 and perfect.recall == 1.0 and untouched,
              f"{perfect.matched_rows}/{perfect.expected}"),
        Check("Validate: an empty key reports recall n/a, never 100%",
              empty.recall is None and empty.precision == 0.0),
    ] + _scoring_checks(result, manifest) + _parsing_checks()


def part3b_validation() -> list[Check]:
    section("PART 3b -- SCANNER VALIDATION MODE (precision and recall on known files)")
    metrics, untouched = validate(SAMPLE_CODEBASE, SAMPLE_EXPECTED_CSV)
    print(f"  Sample folder vs {SAMPLE_EXPECTED_CSV.name} (files untouched: {untouched}):")
    print_metrics(metrics)
    print("  Run on your own files:  py validate_scanner.py <folder> <expected.csv>")
    return validation_checks()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("Usage")[1].strip())
    metrics, untouched = validate(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    print_metrics(metrics)
    print(f"  scanned files unchanged: {untouched}")
    sys.exit(0 if untouched else 2)
