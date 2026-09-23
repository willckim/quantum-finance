"""The migration-register section of pqc_readiness_report.md.

It summarizes the register, prints the priority rule in full (so a reader can check any
row by hand), shows example rows, lists the remediation targets with their sources, and
records the evidence needed to reproduce the rows.
"""

from __future__ import annotations

from collections import Counter

from asset_register import UNCLASSIFIED, AssetRegister
from config import Config
from migration_register import RegisterRow, RunMeta
from remediation import PLANNING_NOTE, REMEDIATIONS
from report_findings import cite

EXAMPLES_PER_LEVEL = 2
REMEDIATION_LABELS = {
    "QV-RSA": "RSA", "QV-ECC": "Elliptic-curve (ECDH, ECDSA, EdDSA, X25519)",
    "QV-DH": "Finite-field Diffie-Hellman", "QV-DSA": "DSA",
    "QV-KEYSIZE": "Hardcoded key size, 2048 bits or more",
    "CW-KEYSIZE": "Key size under 2048 bits", "CW-MD5": "MD5", "CW-SHA1": "SHA-1",
    "CW-CIPHER": "DES, 3DES, RC4", "CW-TLS-LEGACY": "SSL, TLS 1.0 and 1.1",
}


def _counts_table(rows: list[RegisterRow], cfg: Config) -> str:
    by_priority = Counter(r.priority for r in rows)
    unclassified = Counter(r.priority for r in rows if r.classification == UNCLASSIFIED)
    lines = ["| Priority | Rows | of which unclassified |", "|---|---|---|"]
    lines += [f"| {lvl} | {by_priority.get(lvl, 0)} | {unclassified.get(lvl, 0)} |"
              for lvl in cfg.priority_levels]
    return "\n".join(lines + [f"| **Total** | **{len(rows)}** | **{sum(unclassified.values())}** |"])


def _rule_table(cfg: Config, rows: list[RegisterRow]) -> str:
    by_rule = Counter(r.priority_rule for r in rows)
    lines = ["| Order | Rule | Priority | Applies when | Reason | Rows |", "|---|---|---|---|---|---|"]
    lines += [f"| {i} | {r.rule_id} | {r.priority} | {r.describe()} | {r.reason} | "
              f"{by_rule.get(r.rule_id, 0)} |" for i, r in enumerate(cfg.priority_rules, 1)]
    return "\n".join(lines)


def _examples(rows: list[RegisterRow], cfg: Config) -> str:
    picked = [r for lvl in cfg.priority_levels
              for r in [x for x in rows if x.priority == lvl][:EXAMPLES_PER_LEVEL]]
    lines = ["| Row | Priority (rule) | File:line | Detected | Governing data type | "
             "Base exposure |", "|---|---|---|---|---|---|"]
    lines += [f"| {r.row_id} | {r.priority} ({r.priority_rule}) | `{r.file}:{r.line}` | "
              f"{r.rule_id}: `{r.matched_text}` | {r.governing_data_type} | {r.exposure_base} |"
              for r in picked]
    return "\n".join(lines)


def _remediation_table() -> str:
    lines = ["| Finding type | Planning target | Sources |", "|---|---|---|"]
    lines += [f"| {name} | {REMEDIATIONS[rule].action} | {cite(*REMEDIATIONS[rule].source_keys)} |"
              for rule, name in REMEDIATION_LABELS.items()]
    return "\n".join(lines)


def _no_low_note(rows: list[RegisterRow], cfg: Config) -> str:
    """Explain an empty Low level, but only when the rows show why."""
    classified = [r for r in rows if r.classification != UNCLASSIFIED]
    if any(r.priority == "Low" for r in rows) or not classified or any(
            r.exposure_base == "safe" for r in classified):
        return ""
    return (f" No row is Low: at the {cfg.migration_years}-year migration pace, every "
            "registered data type is exposed in the Base window. A faster migration or a "
            "different inventory would change that.")


def register_section(rows: list[RegisterRow], cfg: Config, register: AssetRegister,
                     meta: RunMeta, stale: list[str] = ()) -> str:
    unclassified = sorted({r.file for r in rows if r.classification == UNCLASSIFIED})
    base = next(s for s in cfg.scenarios if s.name == "Base")
    return f"""
## Migration register

The register joins each scanner finding to the **asset register** (`{register.path_label}`,
{len(register.assets)} entries, supplied by people, never inferred), then to the Mosca result
for the data that asset handles, then to the **priority rule** in `{cfg.path_label}`. It adds
no new judgment: exposure comes from the Mosca inputs, and priority from the stated rule.
When a file maps to several data types, the row is governed by the longest shelf life and
says so. Files: `migration_register.xlsx` (filterable, with a summary tab),
`migration_register.csv`, and the CycloneDX 1.7 CBOM `pqc_cbom.cdx.json` {cite("CYCLONEDX-1.7")}.

{_counts_table(rows, cfg)}

**Unclassified: needs owner review.** {len(unclassified)} files have findings but no
asset-register entry: {", ".join(f"`{f}`" for f in unclassified) or "none"}. Their exposure is
unknown, so quantum-vulnerable findings there wait for an owner. Classically weak findings
there are still High, because they do not depend on the data. Register patterns that
matched no scanned file (possibly stale entries): {", ".join(f"`{s}`" for s in stale) or "none"}.

**The priority rule** (the first rule whose conditions all hold decides the row; the Base
window is {base.label}, calculated at {base.crqc_year}). Any row can be checked by hand from
its class, whether the register covers its file, and its Base exposure:

{_rule_table(cfg, rows)}

Priority is a planning aid for ordering the migration, not a risk rating from a certified
assessment.{_no_low_note(rows, cfg)}

**Example rows** (up to {EXAMPLES_PER_LEVEL} per priority; the full list is in the files above):

{_examples(rows, cfg)}

**Remediation targets.** {PLANNING_NOTE}

{_remediation_table()}

**Evidence.** Every row records the file, line, matched text, the SHA-256 of the bytes
scanned, the scan time and tool version, and the SHA-256 of the config and the asset
register. This run: tool {meta.tool_version}, scanned {meta.scan_timestamp}, config
`{meta.config_sha256[:16]}...`, register `{meta.register_sha256[:16]}...`. Rerunning
`run_all.py` with the same inputs reproduces every row except the timestamp.
"""
