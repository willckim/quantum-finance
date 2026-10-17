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
    Part 7  migration_register.py  WHAT TO DO FIRST. One prioritized row per finding:
            asset_register.py      detected crypto, location, data classification (from the
            priority_rules.py      asset register only), Mosca exposure, priority (stated
            remediation.py         rule), remediation and evidence. CSV and xlsx outputs
            register_outputs.py    via register_outputs.py.
    Part 8  cbom.py              The same inventory as a CycloneDX 1.7 CBOM, schema-validated.
    Part 9  tls_check.py         WHAT A VENDOR'S FRONT DOOR NEGOTIATES. One TLS handshake per
            tls_selfcheck.py     listed host (run separately: it is the only part that
            vendor_observed.py   touches the network). run_all.py reads its evidence offline,
            report_tls.py        compares vendor claims with it and grades the checker.
    Part 5  audit_report.py      THE DELIVERABLE. pqc_readiness_report.md (a readiness
            report_findings.py   assessment in audit-finding format, with the register
            report_register.py   section) and pqc_readiness_chart.png.
            report_tls.py
            exposure_chart.py

    Inputs  pqc_config.yaml      Retention periods, migration time, scenario windows,
                                 and the migration priority rules.
            asset_register.yaml  Which data each system or path handles (human-supplied).
            config.py            Loads and validates the config (asset_register.py the
                                 register); anything inconsistent stops the run.
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
    PQC_ASSET_REGISTER=<your_register.yaml> which data each system or path handles
    PQC_VENDOR_OBSERVATIONS=<file.json>    vendor handshakes from `tls_check.py --vendors`
Checks tied to the synthetic samples then report NOT RUN rather than PASS. With a custom
tracker or vendor observations, the report and run log go to pqc_readiness_report.local.md
and pqc_run_log.local.json (gitignored), since they name real vendors and hosts.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import cryptography
import numpy as np
import qiskit
import qiskit_aer

from asset_register import RegisterError, load_register, register_checks, unused_patterns
from audit_report import (DEFAULTS_NOTE, LIMITATION_STATEMENTS, TLS_LIMITATIONS, gating_check,
                          write_report)
from cbom import part8_cbom
from common import (CBOM_JSON, CHART_PNG, HERE, REGISTER_CSV, REGISTER_XLSX, REPORT_MD,
                    RUN_LOG_JSON, TOOL_VERSION, Check, force_utf8_console, not_run,
                    print_checks, scan_root, section)
from config import ConfigError, config_checks, load_config
from crypto_scanner import part3_scan
from exposure_chart import render_chart
from key_exchange import part4_key_exchange
from migration_register import part7_register
from mosca import SAFE, part2_mosca
from register_outputs import output_checks, write_csv, write_xlsx
from remediation import remediation_checks
from report_register import register_section
from report_tls import TlsEvidence
from shor_toy import part1_shor
from sources import SOURCES, VERIFIED
from tls_check import DEMO_OBSERVATIONS, HOSTS_YAML, ObservationsError, git_ignored, load_hosts
from tls_selfcheck import part9_tls, private_run
from validate_scanner import part3b_validation
from vendor_observed import claims_rating
from vendor_review import QUESTIONNAIRE_MD, SAMPLE_TRACKER, TrackerError, part6_vendors

# A run on real vendor data writes the report and log here instead (gitignored).
REPORT_LOCAL = HERE / "pqc_readiness_report.local.md"
RUN_LOG_LOCAL = HERE / "pqc_run_log.local.json"
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


def register_section_check(text: str, rows, levels) -> Check:
    counts = {lvl: sum(r.priority == lvl for r in rows) for lvl in levels}
    section_text = text.split("\n## Migration register", 1)[-1].split("\n## ", 1)[0]
    missing = [lvl for lvl, n in counts.items() if f"| {lvl} | {n} |" not in section_text]
    return Check("Report: migration-register section shows the register's counts by priority",
                 "\n## Migration register" in text and not missing,
                 f"missing {missing}" if missing else f"{len(rows)} rows")


def report_checks(text: str) -> list[Check]:
    findings = re.split(r"\n## Finding \d", text)[1:]
    complete = [all(attr in f for attr in FINDING_ATTRIBUTES) for f in findings]
    cited = set(CITE.findall(text))
    unknown = sorted(cited - SOURCES.keys())
    unlabeled = label_problems(text)
    leaks = LEAK.findall(text)
    limits = text.split("\n## Limitations", 1)[-1] if "\n## Limitations" in text else ""
    checks = [
        Check("Report: 6 findings, each with Condition/Criteria/Cause/Effect/Recommendation",
              len(findings) == 6 and all(complete), f"{sum(complete)}/{len(findings)} complete"),
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
        Check("Report: Limitations section carries the three TLS check statements",
              all(s in limits for s in TLS_LIMITATIONS)),
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
    name = "README: carries the same four core and three TLS Limitations statements"
    if README.is_file():
        readme = README.read_text(encoding="utf-8")
        missing = [s for s in LIMITATION_STATEMENTS + TLS_LIMITATIONS if s not in readme]
        checks.append(Check(name, not missing, f"missing {missing}" if missing else ""))
    else:
        checks.append(not_run(name, "repository README not found"))
    return checks


def tls_report_check(text: str, tls: TlsEvidence) -> Check:
    """Finding 6 shows the computed rating and every vendor's outcome; Appendix E shows
    one row per demo host, or says it was not included."""
    six = text.split("\n## Finding 6", 1)[-1].split("\n## ", 1)[0]
    appendix = text.split("\n## Appendix E", 1)[-1].split("\n## ", 1)[0]
    problems = [] if f"**Rating: {claims_rating(tls.claims)}**" in six else ["rating"]
    problems += [c.vendor for c in tls.claims
                 if f"| {c.vendor} | {c.claimed} | {c.observed} |" not in six]
    if tls.demo:
        problems += [o.host for o in tls.demo if f"| `{o.host}` |" not in appendix]
    elif "Not included in this run" not in appendix:
        problems.append("appendix E claims evidence it does not have")
    return Check("Report: Finding 6 shows the computed rating and each vendor's outcome; "
                 "Appendix E one row per demo host", "\n## Finding 6" in text and not problems,
                 f"missing {problems}" if problems else f"{len(tls.claims)} vendors")


def output_paths(tracker: Path, vendor_obs: Path | None) -> tuple[Path, Path]:
    """(report, run log). Both name vendors and Finding 6 lists their hosts, so a run on
    real vendor data must never write them to the tracked file names."""
    if private_run(tracker, vendor_obs):
        return REPORT_LOCAL, RUN_LOG_LOCAL
    return REPORT_MD, RUN_LOG_JSON


def private_output_check(tracker: Path, vendor_obs: Path | None, hosts: list[str]) -> Check:
    name = "Outputs: runs on real vendor data write the report and log to gitignored names"
    custom, obs = HERE / "my_tracker.csv", HERE / "vendor_observations.local.json"
    cases = {"sample": (output_paths(SAMPLE_TRACKER, None), (REPORT_MD, RUN_LOG_JSON)),
             "custom tracker": (output_paths(custom, None), (REPORT_LOCAL, RUN_LOG_LOCAL)),
             "observations": (output_paths(SAMPLE_TRACKER, obs), (REPORT_LOCAL, RUN_LOG_LOCAL))}
    wrong = [k for k, (got, want) in cases.items() if got != want]
    ignored = [git_ignored(path) for path in (REPORT_LOCAL, RUN_LOG_LOCAL, REPORT_MD)]
    if None in ignored:
        return not_run(name, "git could not be asked")
    if ignored != [True, True, False]:
        wrong.append(f"ignored {ignored}")
    tracked = " ".join(path.read_text(encoding="utf-8") for path in (REPORT_MD, RUN_LOG_JSON)
                       if path.is_file())
    wrong += [f"{h} in a tracked output" for h in hosts if h in tracked]
    return Check(name, not wrong, f"wrong: {wrong}" if wrong else f"{len(cases)} cases")


# ---- audit trail -----------------------------------------------------------------------
def write_run_log(run_at: str, cfg, scan, mosca, kem, ratings, tracker: Path,
                  checks: list[Check], rows, meta, tls: TlsEvidence, observed: Path,
                  report: Path, log_path: Path) -> None:
    """What was read, what rules ran, what was produced, what was skipped."""
    log = {
        "run_timestamp": run_at, "automation": "pqc-readiness-toolkit", "version": TOOL_VERSION,
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
                       "register_rows_by_priority": {lvl: sum(r.priority == lvl for r in rows)
                                                     for lvl in cfg.priority_levels},
                       "kem_profiles": [asdict(p) for p in kem],
                       "tls_demo_by_class": (dict(Counter(o.key_exchange_class or o.status
                                                          for o in tls.demo))
                                             if tls.demo else "not included"),
                       "vendor_claim_checks": {c.vendor: c.outcome for c in tls.claims}},
        "output": {"report": report.name, "chart": CHART_PNG.name,
                   "questionnaire": QUESTIONNAIRE_MD.name, "register_csv": REGISTER_CSV.name,
                   "register_xlsx": REGISTER_XLSX.name, "cbom": CBOM_JSON.name,
                   "vendor_tracker_observed": observed.name},
        "evidence": {"config_sha256": meta.config_sha256,
                     "register_sha256": meta.register_sha256,
                     "scan_timestamp": meta.scan_timestamp,
                     "tls_demo_observations": DEMO_OBSERVATIONS.name if tls.demo else None,
                     "vendor_observations": tls.source or None},
        "exceptions": [{"file": p, "reason": r} for p, r in scan.skipped],
        "self_checks": {"passed": sum(c.passed for c in checks if not c.not_run),
                        "ran": sum(not c.not_run for c in checks),
                        "failed": [c.name for c in checks if not c.passed and not c.not_run],
                        "not_run": [c.name for c in checks if c.not_run]},
    }
    log_path.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


def register_stage(cfg, scan):
    """Parts 7 and 8: the migration register, its CSV/xlsx, and the CBOM."""
    register = load_register(cfg)
    rows, meta, checks = part7_register(cfg, scan, register, scan_root())
    write_csv(REGISTER_CSV, rows)
    write_xlsx(REGISTER_XLSX, rows, cfg, meta, register.path_label)
    checks += (register_checks(cfg) + remediation_checks()
               + output_checks(rows, REGISTER_CSV, REGISTER_XLSX, cfg))
    _, more = part8_cbom(rows, meta, cfg.priority_levels, CBOM_JSON)
    csv_problems = label_problems(REGISTER_CSV.read_text(encoding="utf-8"))
    checks += more + [Check("Outputs: every draft source cited in the CSV carries its label",
                            not csv_problems, f"{len(csv_problems)} unlabeled")]
    stale = unused_patterns(register, sorted(scan.file_sha256))
    return rows, meta, register_section(rows, cfg, register, meta, stale), checks


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
    obs_env = os.environ.get("PQC_VENDOR_OBSERVATIONS", "").strip()
    vendor_obs = Path(obs_env).resolve() if obs_env else None
    try:
        claims, demo, observed, more = part9_tls(tracker, [r.vendor for r in ratings],
                                                 vendor_obs)
    except ObservationsError as exc:
        print(f"VENDOR OBSERVATIONS ERROR: {exc}")
        return 2
    checks += more
    tls = TlsEvidence(tuple(claims), tuple(demo) if demo else None,
                      vendor_obs.name if vendor_obs else "", load_hosts(HOSTS_YAML).delay_seconds)
    try:
        rows, meta, register_text, more = register_stage(cfg, scan)
    except RegisterError as exc:
        print(f"ASSET REGISTER ERROR: {exc}")
        return 2
    checks += more

    section("PART 5 -- READINESS REPORT AND CHART")
    render_chart(cfg, mosca, CHART_PNG)
    report, log_path = output_paths(tracker, vendor_obs)
    text = write_report(report, cfg, mosca, scan, kem, shor, ratings, tracker.name, checks,
                        register_text, tls)
    loaded_hosts = [h for c in claims for h in c.hosts]
    checks += report_checks(text) + [register_section_check(text, rows, cfg.priority_levels),
                                     tls_report_check(text, tls),
                                     private_output_check(tracker, vendor_obs, loaded_hosts)]
    write_run_log(run_at, cfg, scan, mosca, kem, ratings, tracker, checks, rows, meta, tls,
                  observed, report, log_path)
    for path in (report, CHART_PNG, QUESTIONNAIRE_MD, REGISTER_CSV, REGISTER_XLSX,
                 CBOM_JSON, observed, log_path):
        print(f"  wrote {path.relative_to(HERE)}")
    return 0 if print_checks(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
