"""Part 7 -- The migration register: one prioritized row per finding, with its evidence.

Each row joins four things that already exist, and adds no new judgment:

    scanner finding   what cryptography, where (file, line, matched text)
    asset register    what data that file handles       (human-supplied, never inferred)
    Mosca results     how exposed that data is, per scenario window
    priority rule     the first rule in pqc_config.yaml that matches the row

If a file maps to several data types, the row is governed by the longest shelf life and
says so. If no register entry covers the file, the row is "Unclassified: needs owner
review" and gets no exposure at all. Remediation comes from remediation.py, with sources.

EVIDENCE (so a reviewer can rerun the scan and reproduce every row)
    file path, line, matched text, SHA-256 of the exact bytes scanned, scan timestamp,
    tool version, and SHA-256 of the config and of the asset register. Rerunning with
    the same inputs reproduces every row except the timestamp; run_all.py proves it.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from asset_register import (UNCLASSIFIED, AssetRegister, classify, is_shipped, load_register,
                            unused_patterns)
from common import SAMPLE_CODEBASE, TOOL_VERSION, Check, not_run, section
from config import Config, load_config, matches_shipped_defaults
from crypto_scanner import PQ, ScanResult, scan, snapshot
from mosca import SAFE, Assessment, assess
from priority_rules import combinations, first_match
from remediation import remediation_for
from sources import label

SCENARIO_COLUMNS = {"Pessimistic": "exposure_pessimistic", "Base": "exposure_base",
                    "Optimistic": "exposure_optimistic"}


@dataclass(frozen=True)
class RunMeta:
    scan_timestamp: str
    tool_version: str
    config_sha256: str
    register_sha256: str
    scan_root: str


@dataclass(frozen=True)
class RegisterRow:
    row_id: str
    priority: str
    priority_rule: str
    priority_reason: str
    asset_ids: str
    asset_names: str
    owners: str
    file: str
    line: int
    rule_id: str
    category: str
    detected: str
    matched_text: str
    classification: str
    governing_data_type: str
    shelf_life_years: str
    exposure_pessimistic: str
    exposure_base: str
    exposure_optimistic: str
    remediation: str
    remediation_sources: str
    file_sha256: str
    scan_timestamp: str
    tool_version: str
    config_sha256: str
    register_sha256: str


COLUMNS = tuple(RegisterRow.__dataclass_fields__)


def run_meta(cfg: Config, register: AssetRegister, scan_root_label: str) -> RunMeta:
    """The config and register hashes are those taken when they were loaded, so they are
    the hashes of the bytes that were actually parsed (not of a later re-read)."""
    return RunMeta(datetime.now(timezone.utc).isoformat(timespec="seconds"), TOOL_VERSION,
                   cfg.sha256, register.sha256, scan_root_label)


def row_id(path: str, line: int, rule_id: str) -> str:
    """Stable across runs: depends only on the finding, not on how many came before it."""
    return "F-" + hashlib.sha256(f"{path}:{line}:{rule_id}".encode()).hexdigest()[:8]


def _exposure(a: Assessment | None, category: str) -> str:
    """Mosca exposure applies to data behind quantum-vulnerable (or weak) crypto only."""
    if a is None:
        return "n/a (unclassified)"
    if category == PQ:
        return "n/a (not quantum-vulnerable)"
    return "safe" if a.status == SAFE else f"{a.status} (+{a.exposure_years} yr)"


def build_rows(scan_result: ScanResult, cfg: Config, register: AssetRegister,
               meta: RunMeta) -> list[RegisterRow]:
    findings = sorted(scan_result.findings, key=lambda f: (f.path, f.line, f.rule_id))
    rows = []
    for f in findings:
        c = classify(register, f.path)
        per_scenario = ({s.name: assess(c.governing, s, cfg.migration_years, cfg.as_of_year)
                         for s in cfg.scenarios} if c.classified else {})
        base = per_scenario.get("Base")
        rule = first_match(cfg.priority_rules, f.category, c.classified,
                           base.status if base else None)
        fix = remediation_for(f.rule_id)
        rows.append(RegisterRow(
            row_id(f.path, f.line, f.rule_id), rule.priority, rule.rule_id, rule.reason,
            "; ".join(a.asset_id for a in c.assets), "; ".join(a.name for a in c.assets),
            "; ".join(sorted({a.owner for a in c.assets})), f.path, f.line, f.rule_id,
            f.category, f.what, f.match, c.note,
            c.governing.name if c.classified else UNCLASSIFIED,
            str(c.governing.shelf_life) if c.classified else "",
            *(_exposure(per_scenario.get(s), f.category) for s in SCENARIO_COLUMNS),
            fix.action, ", ".join(label(k) for k in fix.source_keys),
            scan_result.file_sha256[f.path], meta.scan_timestamp, meta.tool_version,
            meta.config_sha256, meta.register_sha256))
    order = {level: i for i, level in enumerate(cfg.priority_levels)}
    return sorted(rows, key=lambda r: (order[r.priority], r.file, r.line, r.rule_id))


def comparable(rows: list[RegisterRow]) -> list[tuple]:
    """Rows without the timestamp: what a rerun on the same inputs must reproduce."""
    return [tuple(replace(r, scan_timestamp="").__dict__.values()) for r in rows]


# ======================================================================================
# THE DEMO AND ITS SELF-CHECKS
# ======================================================================================
# Hand-worked from the sample (41 findings, sample_manifest.py) and asset_register.yaml,
# with the default config (Base planning z = 9, migration y = 10):
#   Unregistered: config/gateway_nginx.conf (3 weak, 5 quantum-vulnerable) and
#                 infra/load_balancer.tf (2 weak) -> 10 unclassified rows.
#   R1 High   every classically weak finding, registered or not: 1+1+3+2+2+2 = 11
#   R2 Monitor archive_encrypt.py, all PQC-ready: 9
#   R3 Review  quantum-vulnerable in the unregistered gateway: 5
#   R4 High   LedgerExportSigner.java: GL-EXPORT + CLOSE-BATCH -> M&A documents (18 > 9,
#             EXPOSED NOW) governs its 5 quantum-vulnerable findings: 5
#   R5 Medium tls_client (5) + webhook (2): Customer payment data 5 + 10 > 9; sftp feed (4):
#             Contracts 4 (longer than Bank credentials 2) + 10 > 9: 11
#   R6 Low    none: with a 10-year migration every data type is exposed in Base.
EXPECTED_BY_RULE = {"R1": 11, "R2": 9, "R3": 5, "R4": 5, "R5": 11}
EXPECTED_BY_PRIORITY = {"High": 16, "Medium": 11, "Needs owner review": 5, "Monitor": 9}
EXPECTED_UNCLASSIFIED = {"config/gateway_nginx.conf", "infra/load_balancer.tf"}
# (file, governing data type, Base exposure), worked by hand: x + y - z with z = 9.
EXPECTED_JOINS = {("payments_api/tls_client.py", "Customer payment data", "EXPOSED (+6 yr)"),
                  ("config/sftp_vendor_feed.yaml", "Contracts", "EXPOSED (+5 yr)"),
                  ("batch_jobs/LedgerExportSigner.java", "M&A documents",
                   "EXPOSED NOW (+19 yr)")}
# The 12 kinds of row and the rule the SHIPPED rules must give each (read off the rule
# list by hand: weak -> R1 always; PQC-ready -> R2; unregistered -> R3; then by status).
EXPECTED_RULE_TABLE = {("quantum-vulnerable", True, "EXPOSED NOW"): "R4",
                       ("quantum-vulnerable", True, "EXPOSED"): "R5",
                       ("quantum-vulnerable", True, "safe"): "R6",
                       ("quantum-vulnerable", False, None): "R3",
                       **{("classically weak", k, s): "R1" for k, s in
                          [(True, "EXPOSED NOW"), (True, "EXPOSED"), (True, "safe"),
                           (False, None)]},
                       **{("PQC-ready", k, s): "R2" for k, s in
                          [(True, "EXPOSED NOW"), (True, "EXPOSED"), (True, "safe"),
                           (False, None)]}}


def _rule_checks(cfg: Config) -> list[Check]:
    kinds = combinations()
    table = {combo: first_match(cfg.priority_rules, *combo).rule_id for combo in kinds}
    fired = set(table.values())
    levels = {r.priority for r in cfg.priority_rules if r.rule_id in fired}
    checks = [
        # The kinds come from priority_rules.combinations(); the list they must equal is
        # written out by hand below, so a kind dropped from combinations() is caught.
        Check("Priority: the row kinds checked are exactly the 12 hand-listed ones",
              set(kinds) == set(EXPECTED_RULE_TABLE) and len(kinds) == len(set(kinds)),
              f"{len(set(kinds))} kinds"),
        Check("Priority: every rule fires for some kind of row and every level is reachable",
              fired == {r.rule_id for r in cfg.priority_rules}
              and levels == set(cfg.priority_levels),
              f"{len(fired)} rules, {len(levels)} levels over {len(table)} row kinds")]
    name = "Priority: shipped rules give each of the 12 row kinds its hand-worked rule"
    if not matches_shipped_defaults(cfg):
        return checks + [not_run(name, "config differs from the shipped defaults")]
    wrong = {k: v for k, v in table.items() if EXPECTED_RULE_TABLE.get(k) != v}
    return checks + [Check(name, not wrong and set(table) == set(EXPECTED_RULE_TABLE),
                           f"wrong: {wrong}" if wrong else
                           f"{len(EXPECTED_RULE_TABLE) - len(wrong)}/{len(EXPECTED_RULE_TABLE)}")]


def _never_infer_control(scan_result: ScanResult, cfg: Config, register: AssetRegister,
                         rows: list[RegisterRow], meta: RunMeta) -> Check:
    """Runs on EVERY run, sample or not. For each classified file (up to 20), remove only
    the register entries that cover it and rebuild: its rows must turn unclassified with no
    exposure, even while its neighbours stay classified. A tool that inferred the class from
    anything else (a sibling file, a folder name, the findings) would keep them."""
    name = "Register: removing a file's entries makes it unclassified, never guessed"
    targets = sorted({r.file for r in rows if r.classification != UNCLASSIFIED})[:20]
    if not targets:
        return not_run(name, "no classified file to test with")
    failed = []
    for target in targets:
        keep = tuple(a for a in register.assets if a not in classify(register, target).assets)
        rebuilt = [r for r in build_rows(scan_result, cfg, replace(register, assets=keep), meta)
                   if r.file == target]
        if not rebuilt or not all(r.classification == UNCLASSIFIED and r.asset_ids == ""
                                  and r.exposure_base == "n/a (unclassified)" for r in rebuilt):
            failed.append(target)
    return Check(name, not failed, f"{len(targets) - len(failed)}/{len(targets)} files"
                 + (f"; still classified: {failed}" if failed else ""))


def _sample_checks(rows: list[RegisterRow]) -> list[Check]:
    by_rule = Counter(r.priority_rule for r in rows)
    by_priority = Counter(r.priority for r in rows)
    unclassified = {r.file for r in rows if r.classification == UNCLASSIFIED}
    weak_unreg = [r for r in rows if r.file in EXPECTED_UNCLASSIFIED
                  and r.category == "classically weak"]
    # EVERY row of each named file must carry the hand-worked data type and exposure.
    joins = {(f, g, e) for f, g, e in EXPECTED_JOINS
             if any(r.file == f for r in rows) and all(
                 r.governing_data_type == g and r.exposure_base == e
                 for r in rows if r.file == f)}
    ledger = [r for r in rows if r.file == "batch_jobs/LedgerExportSigner.java"]
    return [
        Check("Register: row counts by rule match the hand-worked sample",
              dict(by_rule) == EXPECTED_BY_RULE, f"{dict(sorted(by_rule.items()))}"),
        Check("Register: row counts by priority match the hand-worked sample",
              dict(by_priority) == EXPECTED_BY_PRIORITY, f"{dict(by_priority)}"),
        Check("Register: exactly the unregistered files are unclassified",
              unclassified == EXPECTED_UNCLASSIFIED, f"{sorted(unclassified)}"),
        Check("Register: weak crypto in an unregistered file is still High (rule R1)",
              bool(weak_unreg) and all(r.priority_rule == "R1" for r in weak_unreg),
              f"{len(weak_unreg)} rows"),
        Check("Register: governing data type and Base exposure match the hand-worked joins",
              joins == EXPECTED_JOINS, f"{len(joins)}/{len(EXPECTED_JOINS)}"),
        Check("Register: a file in several entries uses the longest shelf life and says so",
              bool(ledger) and all("longest shelf life of 3" in r.classification
                                   for r in ledger), ledger[0].classification if ledger else ""),
    ]


def _rescan(root: Path, meta: RunMeta) -> list[RegisterRow]:
    """Everything from disk again: config, register and files, as a reviewer would."""
    cfg = load_config()
    register = load_register(cfg)
    return build_rows(scan(root, meta.scan_root), cfg, register,
                      run_meta(cfg, register, meta.scan_root))


def _evidence_checks(rows: list[RegisterRow], scan_result: ScanResult, root: Path,
                     cfg: Config, register: AssetRegister, meta: RunMeta) -> list[Check]:
    independent = {path: digest for path, (digest, _, _) in snapshot(root).items()}
    rerun = _rescan(root, meta)
    complete = all(len(r.file_sha256) == 64 and r.scan_timestamp and r.tool_version
                   and len(r.config_sha256) == 64 and len(r.register_sha256) == 64
                   and r.matched_text for r in rows)
    order = {level: i for i, level in enumerate(cfg.priority_levels)}
    keys = [(order[r.priority], r.file, r.line, r.rule_id) for r in rows]
    # Control: the same comparison must notice a one-byte change in a scanned file.
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "copy"
        shutil.copytree(root, copy)
        victim = copy / rows[0].file
        victim.write_bytes(victim.read_bytes() + b"\n")
        changed = build_rows(scan(copy, meta.scan_root), cfg, register, meta)
    differs = {a[COLUMNS.index("file")] for a, b in zip(comparable(rows), comparable(changed))
               if a != b}
    return [
        Check("Evidence: every row carries file hash, timestamp, version, config and register "
              "hashes", complete, f"{len(rows)} rows"),
        Check("Evidence: config and register hashes are those of the bytes actually parsed",
              meta.config_sha256 == cfg.sha256 != "" and meta.register_sha256 == register.sha256 != ""),
        Check("Evidence: each row's file hash equals an independent read of the file",
              all(independent.get(r.file) == r.file_sha256 for r in rows)),
        Check("Evidence: a rescan from disk reproduces every row except the timestamp",
              comparable(rerun) == comparable(rows), f"{len(rerun)} rows rescanned"),
        Check("Evidence: the rescan comparison detects a one-byte change (control)",
              differs == {rows[0].file}, f"flagged {sorted(differs)}"),
        Check("Register: each scanner finding appears exactly once",
              Counter((r.file, r.line, r.rule_id) for r in rows)
              == Counter((f.path, f.line, f.rule_id) for f in scan_result.findings),
              f"{len(rows)} rows, {len(scan_result.findings)} findings"),
        Check("Register: row ids are unique and rows are ordered by priority, file, line",
              len({r.row_id for r in rows}) == len(rows) and keys == sorted(keys)),
        Check("Register: a row is classified only when the register covers its file",
              all((r.classification == UNCLASSIFIED) == (r.asset_ids == "") for r in rows)),
    ]


def part7_register(cfg: Config, scan_result: ScanResult, register: AssetRegister,
                   root: Path) -> tuple[list[RegisterRow], RunMeta, list[Check]]:
    section("PART 7 -- MIGRATION REGISTER (findings x asset register x Mosca x priority rule)")
    meta = run_meta(cfg, register, scan_result.root_label)
    rows = build_rows(scan_result, cfg, register, meta)
    by_priority = Counter(r.priority for r in rows)
    print(f"  {len(rows)} rows from {scan_result.files_scanned} files; register "
          f"{register.path_label} ({len(register.assets)} assets).")
    for level in cfg.priority_levels:
        print(f"    {level:<20} {by_priority.get(level, 0):>3}")
    unclassified = sorted({r.file for r in rows if r.classification == UNCLASSIFIED})
    print(f"  Unclassified (needs owner review): {', '.join(unclassified) or 'none'}")
    stale = unused_patterns(register, sorted(scan_result.file_sha256))
    print(f"  Register patterns matching no scanned file: {', '.join(stale) or 'none'}")
    checks = (_rule_checks(cfg) + _evidence_checks(rows, scan_result, root, cfg, register, meta)
              + [_never_infer_control(scan_result, cfg, register, rows, meta)])
    if root == SAMPLE_CODEBASE and is_shipped(register) and matches_shipped_defaults(cfg):
        checks += _sample_checks(rows)
    else:
        checks.append(not_run("Register: hand-worked sample checks",
                              "custom config, register or scan folder"))
    return rows, meta, checks
