"""Part 5 -- pqc_readiness_report.md: the results written up as a readiness assessment.

Parts 1-4 and 6 produce evidence; this module assembles it into a document a controller
or IT auditor could hand to management. It is deliberately framed as a READINESS
ASSESSMENT produced by an open framework, not an audit, attestation or certification:

  * the scope statement at the top says what was assessed, what was not, and which
    inputs are defaults;
  * the required defaults note tells a reader to replace the retention periods first;
  * the Limitations section states what the scanner cannot see;
  * every draft or unverified source is labeled as such at every citation (checked by
    run_all.py citation by citation).

The findings themselves live in report_findings.py. The organization is fictional, and
the report says so on its first line.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from common import SAMPLE_CODEBASE, Check
from config import Config
from crypto_scanner import CW, PQ, QV, ScanResult
from key_exchange import KemProfile
from mosca import EXPOSED_NOW, SAFE, Assessment
from report_findings import (BASE, Verified, base_scenario, cite, finding_1, label,
                             finding_2, finding_3, finding_4, finding_5, finding_ratings,
                             hybrid_cost, observation)
from sources import SOURCES
from vendor_review import Rating

DEFAULTS_NOTE = ("Default retention periods reflect general federal rules. Replace with "
                 "your organization's records retention policy before relying on results.")

# One source of truth for the Limitations text: the report prints it, and run_all.py
# checks that the repository README carries the same four core statements.
LIMITATION_STATEMENTS = (
    "The code scanner was validated on synthetic samples only.",
    "It only sees code and configuration files in the target folder.",
    "It cannot detect cryptography inside third-party vendors or SaaS systems.",
    "A clean scan does not mean an organization is safe.",
)
LIMITATIONS_DETAIL = (
    "Its accuracy on real code is unmeasured until it is graded on files a person has "
    "marked by hand: run `validate_scanner.py <folder> <expected.csv>` for precision and "
    "recall before trusting it.",
    "It does not see live network traffic, compiled binaries, databases, cloud-service "
    "settings, hardware security modules, or cryptography chosen at runtime.",
    "For vendors, this framework relies on questionnaire answers, which are the vendors' "
    "own statements until evidenced.",
    "A clean scan means only that no recognized algorithm name appeared in the files "
    "given. The scanner matches text, so it also flags names inside comments.",
)
LIMITATIONS_MORE = (
    "Mosca results depend entirely on the inputs. Retention periods are a lower bound on "
    "how long data must stay confidential; migration time is one organization-wide "
    "figure; quantum-arrival windows are planning scenarios, not predictions.",
    "Vendor ratings are computed from recorded answers by a stated rule. They are not an "
    "assessment of any vendor's actual controls.",
    "Key exchange timings were measured on one workstation and show order of magnitude "
    "only.",
    "Sources marked draft or unverified are cited as the stated direction of travel, not "
    "as settled requirements.",
    "Classification in the migration register is only as good as the asset register. The "
    "toolkit never infers what data a file protects, so a missing or wrong entry gives a "
    "missing or wrong exposure.",
    "Unclassified findings need owner review: until an owner maps the file to its data, the "
    "exposure of a quantum-vulnerable finding there is unknown.",
    "Migration priority is a planning aid computed by a stated rule, not a risk rating from "
    "a certified assessment.",
)


def limitations_markdown(heading: str = "## Limitations") -> str:
    core = "\n".join(f"* **{s}** {d}" for s, d in zip(LIMITATION_STATEMENTS,
                                                      LIMITATIONS_DETAIL))
    more = "\n".join(f"* {m}" for m in LIMITATIONS_MORE)
    return f"\n{heading}\n\n{core}\n{more}\n"


# ---- front matter -----------------------------------------------------------------------
def _header(cfg: Config, run_at: str, scan: ScanResult, tracker: str) -> str:
    defaults = sum(d.basis_type != "policy" for d in cfg.data_types)
    return f"""# Post-Quantum Cryptography Readiness Assessment

> **Illustrative assessment of a fictional organization ("SampleCo Finance").** The data
> inventory, codebase and vendors are synthetic, written for a public portfolio project.
> No real company, system, vendor or record was examined.

> **{DEFAULTS_NOTE}**

## Scope and status of this document

This is a readiness assessment produced by an open framework. It is **not an audit, an
attestation or a certification, and it expresses no assurance** on any control. Findings
use the Condition / Criteria / Cause / Effect / Recommendation format for readability.

| | |
|---|---|
| **Assessed** | (1) {len(cfg.data_types)} finance data types against Mosca's inequality; (2) code and configuration text in `{scan.root_label}/` ({scan.files_scanned} files) for cryptographic algorithm names; (3) {tracker}: vendor questionnaire answers as recorded; (4) key exchange sizes and timings on one workstation |
| **Not assessed** | Live network endpoints, production systems, compiled software, databases, cloud-service settings, hardware security modules, key-management procedures, vendor controls beyond their recorded answers, and whether any control operates effectively |
| **Default inputs** | From `{cfg.path_label}`: {defaults} of {len(cfg.data_types)} shelf lives use default rules or assumptions (not organization policy); migration time {cfg.migration_years} years {cite(*cfg.migration_sources)}; quantum-arrival planning windows {", ".join(s.label for s in cfg.scenarios)} |
| Assessment as of | {cfg.as_of_year} |
| Generated | {run_at} by `run_all.py` in this folder (all figures reproducible) |
"""


def _executive_summary(cfg: Config, mosca: dict[str, list[Assessment]], scan: ScanResult,
                       kem: list[KemProfile], ratings: list[Rating]) -> str:
    wire, ms = hybrid_cost(kem)
    base, base_s = mosca[BASE], base_scenario(cfg)
    exposed = sum(a.status != SAFE for a in base)
    now = [a.data.name for a in base if a.status == EXPOSED_NOW]
    totals = scan.by_category()
    high = sum(r.level == "High" for r in ratings)
    now_text = (f"{', '.join(now)} would be exposed even with an instant migration."
                if now else "None would be exposed with an instant migration.")
    table = "\n".join(f"| {n} | {title} | {level} |" for n, (title, level)
                      in enumerate(finding_ratings(mosca, scan, ratings), start=1))
    return f"""
## Executive summary

A cryptographically relevant quantum computer (CRQC) could break the RSA and
elliptic-curve cryptography widely used to protect data in transit today. US
agencies warn that data recorded now could be decrypted later {cite("CISA-HNDL")}, so the
risk is relevant today for any record that must stay confidential beyond a CRQC's
possible arrival. No one knows when that will be; the analysis uses three planning
windows instead.

* **Exposure.** In the {base_s.label} planning scenario, **{exposed} of
  {len(base)} data types** fail Mosca's inequality with a {cfg.migration_years}-year
  migration. {now_text}
* **Code and configuration.** The scanner reported **{totals[CW]} classically weak**,
  **{totals[QV]} quantum-vulnerable** and **{totals[PQ]} PQC-ready** usages. These are
  text matches{" on a synthetic sample" if scan.root_label == SAMPLE_CODEBASE.name else ""},
  not confirmed deployments.
* **Vendors.** The recorded answers of {len(ratings)} vendors produce **{high} High**
  ratings under the stated rule (Finding 5); answers are claims until evidenced.
* **Cost of the fix.** On one workstation, hybrid key exchange took {wire:,} bytes and
  about {ms:.1f} ms per handshake (Appendix C), which suggests computation and bandwidth
  are unlikely to be the main barriers.

| # | Finding | Rating |
|---|---|---|
{table}

Ratings are illustrative priorities: **High** = known weakness or exposure that migration
speed alone cannot remove, address within 90 days; **Medium** = exposure that grows with
delay, plan within 6 months; **Low** = improvement.
"""


# ---- appendices ------------------------------------------------------------------------
def _appendix_b(scan: ScanResult, verified: Verified) -> str:
    rows = "\n".join(f"| {f.category} | `{f.path}` | {f.line} | {f.rule_id} | {f.what} |"
                     for cat in (CW, QV, PQ) for f in scan.findings if f.category == cat)
    skipped = ("None." if not scan.skipped else
               "; ".join(f"`{p}` ({why})" for p, why in scan.skipped))
    untouched = ("every file hashed before and after the scan, unchanged"
                 if verified("Scanner: read-only") else
                 "WARNING: the before/after hash comparison did not confirm the files were "
                 "untouched; see the run's self-checks")
    return f"""
## Appendix B: Cryptographic inventory (scanner output, text matches)

Scope: {scan.files_scanned} files under `{scan.root_label}/`, read-only ({untouched}).
Files not scanned: {skipped}

| Class | File | Line | Rule | What |
|---|---|---|---|---|
{rows}
"""


def gating_check() -> Check:
    """Sentences that assert a verified fact must switch to a WARNING when the check fails."""
    scan = ScanResult("sample_codebase", files_scanned=1)
    ok, bad = _appendix_b(scan, lambda _: True), _appendix_b(scan, lambda _: False)
    return Check("Report: verified-fact sentences switch to WARNING when their check fails",
                 "WARNING" in bad and "WARNING" not in ok and "unchanged" in ok)


def _appendix_c(kem: list[KemProfile], verified: Verified) -> str:
    rows = "\n".join(f"| {p.name} | {p.public_key_bytes:,} | {p.ciphertext_bytes:,} | "
                     f"{p.keygen_us:.0f} | {p.encap_us:.0f} | {p.decap_us:.0f} | "
                     f"{p.quantum_safe} |" for p in kem)
    sizes_ok = (verified("KEM: ML-KEM-768 sizes match") and verified("KEM: hybrid sizes")
                and verified("KEM: hybrid byte order"))
    sizes = ("Sizes and byte order were checked against FIPS 203 Table 3 and RFC 10024"
             if sizes_ok else "WARNING: sizes or byte order did NOT match FIPS 203 Table 3 "
             "and RFC 10024 in this run")
    return f"""
## Appendix C: Key exchange comparison (measured on one workstation)

Medians of repeated runs; the order of magnitude matters, not the exact figure. {sizes}
{cite("FIPS-203", "RFC-10024")}.

| Scheme | Public key (bytes) | Ciphertext (bytes) | Keygen (us) | Encapsulate (us) | Decapsulate (us) | Quantum-safe |
|---|---|---|---|---|---|---|
{rows}

Hybrid key exchange is the construction standardized for the transition in TLS
{cite("RFC-10024")}: an attacker must break both components, so X25519 still protects the
traffic if a flaw is found in the newer ML-KEM, and ML-KEM protects it against a quantum
computer. The UK NCSC treats hybrid as an interim measure on the way to PQC-only
{cite("NCSC-HYBRID")}.
"""


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _appendix_d(cfg: Config, shor: dict) -> str:
    scen = "\n".join(f"* **{s.label}**, calculated at {s.crqc_year}. {_cap(s.basis)}. "
                     f"{cite(*s.source_keys)}" for s in cfg.scenarios)
    inputs = "\n".join(f"* **{d.name}**: {d.shelf_life} years ({d.basis_type}). {d.basis}. "
                       f"{cite(*d.source_keys)}" for d in cfg.data_types)
    shor_text = (f"A toy Shor's algorithm in this toolkit factored {shor['N']} = "
                 f"{shor['factors'][0]} x {shor['factors'][1]} from a quantum-measured period "
                 f"of {shor['period']}." if shor["factors"] else
                 f"The toy Shor's algorithm did not recover the factors of {shor['N']} this run.")
    return f"""
## Appendix D: Inputs, scenarios and the scale of the threat

**Quantum-arrival planning scenarios.** Windows of years built around cited estimates,
named from the defender's point of view ("optimistic" = the CRQC arrives late). They are
planning scenarios, not predictions.

{scen}

**Shelf-life inputs** (from `{cfg.path_label}`; replace with the organization's retention
schedule):

{inputs}

**Why the threat is taken seriously.** {shor_text} That demonstrates the mechanism, not
the timeline. {shor['rsa_2048']} {cite("GIDNEY-2025", "GIDNEY-EKERA-2019")}
"""


def _references(text: str) -> str:
    used = [s for s in SOURCES.values() if f"[{s.key}]" in text]
    lines = [f"* **{label(s.key)}** {s.citation}. <{s.url}>" for s in used]
    return "\n## References\n\n" + "\n".join(lines) + "\n"


def write_report(path: Path, cfg: Config, mosca: dict[str, list[Assessment]],
                 scan: ScanResult, kem: list[KemProfile], shor: dict,
                 ratings: list[Rating], tracker: str, checks: Sequence[Check],
                 register_part: str = "") -> str:
    """Write the report. Sentences that assert a verified fact consult `checks` first."""
    def verified(prefix: str) -> bool:
        found = [c for c in checks if c.name.startswith(prefix)]
        return bool(found) and all(c.passed and not c.not_run for c in found)

    run_at = datetime.now().isoformat(timespec="seconds")
    body = "".join((
        _header(cfg, run_at, scan, tracker), _executive_summary(cfg, mosca, scan, kem, ratings),
        finding_1(cfg, mosca), finding_2(scan), finding_3(scan, kem), finding_4(scan),
        finding_5(cfg, ratings, tracker), observation(scan), register_part,
        limitations_markdown(),
        "\n## Appendix A: Exposure chart\n\n![Mosca exposure timeline by data type across "
        "three planning scenarios](pqc_readiness_chart.png)\n",
        _appendix_b(scan, verified), _appendix_c(kem, verified), _appendix_d(cfg, shor)))
    text = body + _references(body)
    path.write_text(text, encoding="utf-8")
    return text
