"""Shared settings and helpers for the PQC readiness toolkit.

Every other module imports from here. Nothing in this file belongs to one part: it holds
the run-wide plumbing (where the scanner points, where outputs go, seeds and repetition
counts) and the small tools every part uses (self-check records, section headers,
console setup).

WHERE THE ASSESSMENT INPUTS LIVE
--------------------------------
Every number an assessor might challenge (the as-of year, the migration time, each
retention period, the quantum-arrival scenarios) lives in pqc_config.yaml, loaded and
validated by config.py, so an organization can substitute its own without editing code.
Two environment variables point the run elsewhere:

    PQC_CONFIG      an alternative config file (default: pqc_config.yaml next to this file)
    PQC_SCAN_ROOT   folder the crypto scanner walks (default: the synthetic sample)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


# ----------------------------- plumbing ------------------------------------
HERE = Path(__file__).resolve().parent

# The scanner is read-only and can point at any folder, but this project only ever demos
# it on the synthetic sample next to this file. Point it elsewhere with PQC_SCAN_ROOT.
SAMPLE_CODEBASE = HERE / "sample_codebase"

SEED = 2026
SHOR_SHOTS = 2_048                       # shots for the sampled Shor run
KEM_TIMING_ROUNDS = 1_000                # timed repetitions per key-exchange operation

# Recorded in every register row and in the CBOM, so a rerun can be matched to its tool.
TOOL_VERSION = "3.1.0"

REPORT_MD = HERE / "pqc_readiness_report.md"
CHART_PNG = HERE / "pqc_readiness_chart.png"
RUN_LOG_JSON = HERE / "pqc_run_log.json"
REGISTER_CSV = HERE / "migration_register.csv"
REGISTER_XLSX = HERE / "migration_register.xlsx"
CBOM_JSON = HERE / "pqc_cbom.cdx.json"


def scan_root() -> Path:
    override = os.environ.get("PQC_SCAN_ROOT", "").strip()
    return Path(override).resolve() if override else SAMPLE_CODEBASE


# ======================================================================================
# SHARED TOOLS
# ======================================================================================

@dataclass(frozen=True)
class Check:
    """One self-verification result. run_all.py exits non-zero if any check fails.

    not_run marks a check that could not see its subject (for example, the planted-answer
    checks when the scanner points at another folder). It is reported as NOT RUN, counted
    separately and logged, never folded into the passes.
    """

    name: str
    passed: bool
    detail: str = ""
    not_run: bool = False


def not_run(name: str, reason: str) -> Check:
    return Check(name, False, reason, not_run=True)


def section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def print_checks(checks: Sequence[Check]) -> bool:
    section("SELF-VERIFICATION")
    for check in checks:
        detail = f"  ({check.detail})" if check.detail else ""
        label = "NOT RUN" if check.not_run else ("PASS" if check.passed else "FAIL")
        print(f"  [{label}] {check.name}{detail}")
    ran = [check for check in checks if not check.not_run]
    passed = sum(check.passed for check in ran)
    skipped = len(checks) - len(ran)
    print(f"\n  {passed}/{len(ran)} checks passed" + (f", {skipped} NOT RUN." if skipped else "."))
    return passed == len(ran)


def force_utf8_console() -> None:
    # GOTCHA (Windows): when output is piped, Python uses cp1252 and Qiskit's circuit
    # drawer (box-drawing characters) cannot print. Force UTF-8 for this process.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
