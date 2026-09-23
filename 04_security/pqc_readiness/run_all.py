"""Post-quantum cryptography (PQC) readiness toolkit for finance data -- a learning project.

THE PROBLEM IN ONE PARAGRAPH
----------------------------
Almost every encrypted connection today agrees its keys with RSA or elliptic-curve
cryptography. A large enough quantum computer running Shor's algorithm would break both.
Nobody has that machine yet, but an adversary does not need it yet: traffic recorded
today could be decrypted whenever the machine arrives ("harvest now, decrypt later").
Financial records carry legal retention periods of 4 to 18 years, so a record sent today
may still be sensitive when that happens. Governments have started to set migration
deadlines, which turns this into controls work: inventory the cryptography (your own and
your vendors'), rank the data by how long it must stay secret, and migrate the riskiest
first.

WHAT EACH PART DOES, AND WHERE IT LIVES
---------------------------------------
    Part 1  shor_toy.py          WHY RSA BREAKS. Shor's algorithm factors 15 = 3 x 5 on a
                                 simulated quantum computer, with the classical steps shown.
    Part 2  mosca.py             WHAT IS AT RISK. Mosca's inequality over the data inventory
                                 and planning scenarios in pqc_config.yaml.
    Part 3  crypto_scanner.py    WHERE THE CRYPTO IS. A read-only scanner that inventories a
                                 codebase: quantum-vulnerable, classically weak, PQC-ready.
            validate_scanner.py  HOW FAR TO TRUST IT. Precision and recall against a CSV of
                                 findings marked by hand.
    Part 4  key_exchange.py      WHAT TO MIGRATE TO. X25519 vs ML-KEM-768 vs the hybrid,
                                 measured: key sizes, ciphertext sizes and timings.
    Part 6  vendor_review.py     WHAT THE SCANNER CANNOT SEE. Vendor questionnaire, tracker
                                 and computed vendor ratings.
    Part 5  audit_report.py      THE DELIVERABLE. pqc_readiness_report.md (a readiness
            report_findings.py   assessment in audit-finding format) and
            exposure_chart.py    pqc_readiness_chart.png.

    Inputs  pqc_config.yaml      Retention periods, migration time, scenario windows.
            config.py            Loads and validates it.
            sources.py           Every external number with its citation and status.
    Samples sample_codebase/, sample_manifest.py, sample_expected_findings.csv,
            sample_vendor_tracker.csv, vendor_tracker_template.csv (all synthetic)

Start with the report (pqc_readiness_report.md), then read the parts in order. Every
part returns self-checks; this script prints them and exits non-zero if any fails. Each
check's expected value comes from an independent source (a standard's table, a
brute-force calculation, a hand-worked table, or a planted answer key), never from the
output being checked.

Run:  py run_all.py        (about 15 seconds; writes the report, chart, questionnaire and log)
Point it at your own inputs without editing code:
    PQC_CONFIG=<your_config.yaml>          retention periods, migration time, scenarios
    PQC_VENDOR_TRACKER=<your_tracker.csv>  vendor answers (columns as in the template)
    PQC_SCAN_ROOT=<folder>                 code to scan (read-only)
Checks tied to the synthetic samples then report NOT RUN rather than PASS.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import cryptography
import numpy as np
import qiskit
import qiskit_aer

from audit_report import DEFAULTS_NOTE, LIMITATION_STATEMENTS, gating_check, write_report
from common import (CHART_PNG, HERE, REPORT_MD, RUN_LOG_JSON, Check, force_utf8_console,
                    not_run, print_checks, scan_root, section)
from config import ConfigError, config_checks, load_config
from crypto_scanner import part3_scan
from exposure_chart import render_chart
from key_exchange import part4_key_exchange
from mosca import SAFE, part2_mosca
from shor_toy import part1_shor
from sources import SOURCES, VERIFIED
from validate_scanner import part3b_validation
from vendor_review import QUESTIONNAIRE_MD, SAMPLE_TRACKER, TrackerError, part6_vendors

VERSION = "2.0.0"
EM_DASH = chr(0x2014)      # banned in every output; written as a code point, not the glyph
README = HERE.parent.parent / "README.md"
FINDING_ATTRIBUTES = ("**Condition.**", "**Criteria.**", "**Cause.**", "**Effect.**",
                      "**Recommendation.**")
CITE = re.compile(r"\[([A-Z0-9][A-Z0-9-]+)\]")
# Machine paths (drive letters, UNC shares, home folders) and email addresses. The
# lookbehind keeps "https://" from reading as drive "s:".
LEAK = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[A-Za-z]|~/|/Users/|/home/"
                  r"|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")


# ---- report checks ---------------------------------------------------------------------
def label_problems(text: str) -> list[str]:
    """Every citation of a draft or unverified source must be followed by its label.

    Checked per CITATION, not per paragraph: one "(draft)" in a paragraph must not cover
    a second draft cited next to it.
    """
    problems = []
    for key, source in SOURCES.items():
        if source.status == VERIFIED:
            continue
        for m in re.finditer(re.escape(f"[{key}]"), text):
            if not text.startswith(f" ({source.status})", m.end()):
                problems.append(f"[{key}]: {text[max(0, m.start() - 50):m.end()]!r}")
    return problems


def _label_control(text: str) -> bool:
    """Remove the label from ONE citation and require exactly one problem to appear.

    A check that looked for the word anywhere nearby, or anywhere in the document, would
    still find another "(draft)" and pass; so it would fail this control.
    """
    drafts = [(k, s.status) for k, s in SOURCES.items() if s.status != VERIFIED]
    target = next((f"[{k}] ({status})" for k, status in drafts
                   if f"[{k}] ({status})" in text), None)
    if target is None:
        return False
    mutated = text.replace(target, target.split(" (")[0], 1)
    key, status = drafts[0]
    # The label nearby but not attached, and a word that merely contains it: both must fail.
    near = f"see [{key}], which is still a {status} ({status}), and was {status}ed in 2024"
    return (len(label_problems(mutated)) == len(label_problems(text)) + 1
            and len(label_problems(near)) == 1)


def report_checks(text: str) -> list[Check]:
    findings = re.split(r"\n## Finding \d", text)[1:]
    complete = [all(attr in f for attr in FINDING_ATTRIBUTES) for f in findings]
    cited = set(CITE.findall(text))
    unknown = sorted(cited - SOURCES.keys())
    unlabeled = label_problems(text)
    leaks = LEAK.findall(text)
    limits = text.split("\n## Limitations", 1)[-1] if "\n## Limitations" in text else ""
    checks = [
        Check("Report: 5 findings, each with Condition/Criteria/Cause/Effect/Recommendation",
              len(findings) == 5 and all(complete), f"{sum(complete)}/{len(findings)} complete"),
        Check("Report: every bracketed citation resolves to the source registry",
              bool(cited) and not unknown, f"{len(cited)} sources cited"
              + (f"; UNKNOWN {unknown}" if unknown else "")),
        Check("Report: every citation of a draft or unverified source carries its label",
              not unlabeled, f"{len(unlabeled)} unlabeled" + (f": {unlabeled}" if unlabeled
                                                                else "")),
        Check("Report: the label check catches one unlabeled draft citation (control)",
              _label_control(text)),
        Check("Report: opens with the defaults note and a scope statement",
              DEFAULTS_NOTE in text.split("## Executive summary")[0]
              and "## Scope and status" in text and "Not assessed" in text),
        Check("Report: Limitations section carries the four required statements",
              all(s in limits for s in LIMITATION_STATEMENTS)),
        Check("Report: no machine paths, emails or em dashes in the output",
              not leaks and EM_DASH not in text, f"{len(leaks)} leaks {leaks[:3]}"),
        Check("Report: the leak check catches a path, a share, a home folder and an email",
              all(LEAK.search(s) for s in (r"C:\Users\x", "D:/data", r"\\server\share",
                                           "~/notes", "someone@example.com"))
              and not LEAK.search("see https://example.org and `sample_codebase/`")),
        gating_check(),
        Check("Report: chart written and non-trivial",
              CHART_PNG.is_file() and CHART_PNG.stat().st_size > 20_000,
              f"{CHART_PNG.stat().st_size:,} bytes" if CHART_PNG.is_file() else "missing"),
    ]
    name = "README: carries the same four Limitations statements as the report"
    if README.is_file():
        readme = README.read_text(encoding="utf-8")
        missing = [s for s in LIMITATION_STATEMENTS if s not in readme]
        checks.append(Check(name, not missing, f"missing {missing}" if missing else ""))
    else:
        checks.append(not_run(name, "repository README not found"))
    return checks


# ---- audit trail -----------------------------------------------------------------------
def write_run_log(run_at: str, cfg, scan, mosca, kem, ratings, tracker: Path,
                  checks: list[Check]) -> None:
    """What was read, what rules ran, what was produced, what was skipped."""
    log = {
        "run_timestamp": run_at, "automation": "pqc-readiness-toolkit", "version": VERSION,
        "environment": {"python": sys.version.split()[0], "qiskit": qiskit.__version__,
                        "qiskit_aer": qiskit_aer.__version__, "numpy": np.__version__,
                        "cryptography": cryptography.__version__},
        "inputs": {"config": cfg.path_label, "as_of_year": cfg.as_of_year,
                   "migration_years": cfg.migration_years,
                   "scenarios": {s.name: {"window": list(s.window), "planning_year": s.crqc_year}
                                 for s in cfg.scenarios},
                   "vendor_tracker": tracker.name, "scan_root": scan.root_label},
        "source": {"files_read": scan.files_scanned, "data_types": len(cfg.data_types),
                   "vendors": len(ratings)},
        "processing": {"findings_by_class": dict(scan.by_category()),
                       "exposed_by_scenario": {name: sum(a.status != SAFE for a in rows)
                                               for name, rows in mosca.items()},
                       "vendor_ratings": {r.vendor.name: r.level for r in ratings},
                       "kem_profiles": [asdict(p) for p in kem]},
        "output": {"report": REPORT_MD.name, "chart": CHART_PNG.name,
                   "questionnaire": QUESTIONNAIRE_MD.name},
        "exceptions": [{"file": p, "reason": r} for p, r in scan.skipped],
        "self_checks": {"passed": sum(c.passed for c in checks if not c.not_run),
                        "ran": sum(not c.not_run for c in checks),
                        "failed": [c.name for c in checks if not c.passed and not c.not_run],
                        "not_run": [c.name for c in checks if c.not_run]},
    }
    RUN_LOG_JSON.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    force_utf8_console()
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 2
    tracker_env = os.environ.get("PQC_VENDOR_TRACKER", "").strip()
    tracker = Path(tracker_env).resolve() if tracker_env else SAMPLE_TRACKER
    run_at = datetime.now().isoformat(timespec="seconds")
    print("=" * 88)
    print("POST-QUANTUM CRYPTOGRAPHY READINESS -- finance data, synthetic organization")
    print(f"Run {run_at} | qiskit {qiskit.__version__} | aer {qiskit_aer.__version__} | "
          f"numpy {np.__version__} | cryptography {cryptography.__version__}")
    print("=" * 88)

    shor, checks = part1_shor()
    mosca, more = part2_mosca(cfg)
    checks += config_checks() + more
    scan, more = part3_scan(scan_root())
    checks += more + part3b_validation()
    kem, more = part4_key_exchange()
    checks += more
    try:
        ratings, more = part6_vendors(cfg, tracker)
    except (TrackerError, OSError) as exc:
        print(f"VENDOR TRACKER ERROR ({tracker.name}): {exc}")
        return 2
    checks += more

    section("PART 5 -- READINESS REPORT AND CHART")
    render_chart(cfg, mosca, CHART_PNG)
    text = write_report(REPORT_MD, cfg, mosca, scan, kem, shor, ratings, tracker.name, checks)
    checks += report_checks(text)
    write_run_log(run_at, cfg, scan, mosca, kem, ratings, tracker, checks)
    for path in (REPORT_MD, CHART_PNG, QUESTIONNAIRE_MD, RUN_LOG_JSON):
        print(f"  wrote {path.relative_to(HERE)}")
    return 0 if print_checks(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
