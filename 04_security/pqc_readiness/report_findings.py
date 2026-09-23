"""The five findings of pqc_readiness_report.md, in audit-finding format.

Each finding uses the five attributes familiar from internal audit (Condition, Criteria,
Cause, Effect, Recommendation) because they make a gap easy to act on. They are written as
ASSESSMENT observations, not audit findings: causes are stated as likely causes to be
confirmed with system owners, effects as what could happen, and vendor answers as the
vendors' own statements.

Every number is computed from the run. Sentences that assert a verified fact (files
unchanged, sizes match a standard) consult the self-checks first; sentences that
describe the synthetic sample specifically are dropped when another folder is scanned.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

from common import SAMPLE_CODEBASE
from config import Config
from crypto_scanner import CW, PQ, QV, ScanResult
from key_exchange import KemProfile
from mosca import EXPOSED_NOW, SAFE, Assessment
from sources import label
from vendor_review import Rating

BASE = "Base"
Verified = Callable[[str], bool]      # "did every self-check with this name prefix pass?"

SAMPLE_WEAK = (" They include TLS 1.0/1.1 on a customer portal and a settlement client, "
               "SHA-1 and MD5 digests, 3DES, and a 1024-bit RSA key on a vendor file "
               "transfer.")
SAMPLE_HARDCODED = ' (for example, `key_size=2048`, `getInstance("DSA")`, `RS256`)'


def cite(*keys: str) -> str:
    """Every citation goes through here, so a draft can never be cited without its label.
    run_all.py checks the finished report for any citation that slipped past."""
    return ", ".join(label(k) for k in keys)


def _files(scan: ScanResult, category: str) -> list[str]:
    return sorted({f.path for f in scan.findings if f.category == category})


def _on_sample(scan: ScanResult, sentence: str) -> str:
    return sentence if scan.root_label == SAMPLE_CODEBASE.name else ""


def _rule_counts(scan: ScanResult, category: str) -> str:
    counts = Counter(f.rule_id for f in scan.findings if f.category == category)
    return ", ".join(f"{rule} ({n})" for rule, n in sorted(counts.items())) or "none"


def hybrid_cost(kem: list[KemProfile]) -> tuple[int, float]:
    """(bytes on the wire, milliseconds of computation) for one hybrid handshake."""
    h = next(p for p in kem if "Hybrid" in p.name)
    return h.public_key_bytes + h.ciphertext_bytes, (h.keygen_us + h.encap_us + h.decap_us) / 1e3


def base_scenario(cfg: Config):
    return next(s for s in cfg.scenarios if s.name == BASE)


# ---- ratings: computed from the results, never typed in -------------------------------
def rating_1(mosca: dict[str, list[Assessment]]) -> str:
    """High if anything is exposed in Base (or exposed now anywhere); Medium if only in
    Pessimistic; Low if Mosca's inequality holds everywhere."""
    if any(a.status != SAFE for a in mosca[BASE]) or any(
            a.status == EXPOSED_NOW for rows in mosca.values() for a in rows):
        return "High"
    return "Medium" if any(a.status != SAFE for rows in mosca.values() for a in rows) else "Low"


def rating_2(scan: ScanResult) -> str:
    return "High" if scan.by_category()[CW] else "Low"


def rating_3(scan: ScanResult) -> str:
    return "Medium" if scan.by_category()[QV] else "Low"


RATING_4 = "Medium"     # no inventory existed: a stated scenario assumption, see Finding 4


def vendor_finding_rating(ratings: list[Rating]) -> str:
    """Finding 5 takes the rating of its worst vendor."""
    levels = {r.level for r in ratings}
    return next((level for level in ("High", "Medium") if level in levels), "Low")


def finding_ratings(mosca, scan: ScanResult, ratings: list[Rating]) -> list[tuple[str, str]]:
    """(title, rating) for the executive-summary table, from the same functions."""
    return [(TITLES[0], rating_1(mosca)), (TITLES[1], rating_2(scan)),
            (TITLES[2], rating_3(scan)), (TITLES[3], RATING_4),
            (TITLES[4], vendor_finding_rating(ratings))]


TITLES = ("Long-lived financial records may be exposed to harvest-now, decrypt-later attacks",
          "Classically weak cryptography appears in code and configuration",
          "Quantum-vulnerable algorithms are hardcoded across applications",
          "No maintained cryptographic inventory or migration plan",
          "Vendor post-quantum readiness is not yet evidenced")


# ---- Finding 1 -------------------------------------------------------------------------
def _cell(a: Assessment) -> str:
    if a.status == SAFE:
        return "safe"
    return (f"**Exposed now** (+{a.exposure_years})" if a.status == EXPOSED_NOW
            else f"Exposed (+{a.exposure_years})")


def _exposure_table(cfg: Config, mosca: dict[str, list[Assessment]]) -> str:
    head = " | ".join(f"{s.label}, at {s.crqc_year}" for s in cfg.scenarios)
    rows = "\n".join(
        f"| {a.data.name}{' (ASSUMPTION)' if a.data.assumption else ''} | {a.x} | "
        f"{a.data.basis_type} {cite(*a.data.source_keys)} | "
        + " | ".join(_cell(mosca[s.name][i]) for s in cfg.scenarios) + " |"
        for i, a in enumerate(mosca[BASE]))
    return (f"| Data type | Shelf life (yrs) | Basis | {head} |\n"
            f"|---|---|---|{'---|' * len(cfg.scenarios)}\n{rows}")


def _priority(mosca: dict[str, list[Assessment]]) -> str:
    base = mosca[BASE]
    now = [a.data.name for a in base if a.status == EXPOSED_NOW]
    if now:
        return f"the types exposed even with an instant migration ({', '.join(now)})"
    exposed = sorted((a for a in base if a.status != SAFE), key=lambda a: -a.exposure_years)
    if exposed:
        return f"the most exposed types ({', '.join(a.data.name for a in exposed[:3])})"
    return "the longest-lived data types, before the margin shrinks"


def finding_1(cfg: Config, mosca: dict[str, list[Assessment]]) -> str:
    y = cfg.migration_years
    retention = sorted({k for d in cfg.data_types if d.basis_type == "regulation"
                        for k in d.source_keys})
    retention_text = (f"Retention rules {cite(*retention)} set a minimum period each "
                      "record must be kept, and therefore protected." if retention else
                      "The shelf lives come from the organization's own schedule.")
    return f"""
## Finding 1: {TITLES[0]}

**Rating: {rating_1(mosca)}**

**Condition.** Each data type was given a confidentiality shelf life from the inputs in
`{cfg.path_label}` and tested against Mosca's inequality (shelf life + migration time >
years until a CRQC) in three planning scenarios, with a {y}-year migration. The scenario
windows are planning ranges, not predictions; each is calculated at the stated year.

{_exposure_table(cfg, mosca)}

"+N" is the number of years a record would remain sensitive after a CRQC could decrypt
it. "Exposed now" means the shelf life alone exceeds the time to the planning year: even
a migration completed today would not protect records already sent or stored under RSA
or elliptic-curve cryptography. Rows marked ASSUMPTION have no retention rule behind them.

**Criteria.** Mosca's inequality {cite("MOSCA-2015")}. US agency guidance describes
harvest-now, decrypt-later collection as a current concern {cite("CISA-HNDL", "EO-14412")}.
NIST's draft transition timeline, NIST IR 8547 (draft), would deprecate 112-bit RSA and
ECC after 2030 and disallow them after 2035 {cite("NIST-IR-8547")}. {retention_text}

**Cause.** Likely cause, to be confirmed with the data owners: retention periods are set
by law and records management, while encryption choices are made by IT without reference
to how long the data must stay secret, and data is not classified by confidentiality
lifetime.

**Effect.** Records transmitted or stored under RSA or elliptic-curve key exchange today
could become readable within their retention period, which could disclose deal terms,
employee PII, tax and audit evidence, or customer payment data. Because recording
traffic leaves no trace, the organization might not learn of it until the data is used.

**Recommendation.**
1. Replace the default shelf lives with the organization's records retention schedule,
   and add a confidentiality-lifetime attribute to the data classification policy.
2. Prioritize the systems that carry {_priority(mosca)} for hybrid key exchange
   (X25519MLKEM768, {cite("RFC-10024")}).
3. Re-run this analysis at least annually, and when threat estimates move {cite("GRI-2025")}.

**Management response.** *[To be completed by the process owner: action, owner, target date.]*
"""


# ---- Findings 2 to 4: from the code scan ----------------------------------------------
def finding_2(scan: ScanResult) -> str:
    return f"""
## Finding 2: {TITLES[1]}

**Rating: {rating_2(scan)}**

**Condition.** The scanner reported {scan.by_category()[CW]} usages of algorithms or protocols
with known practical weaknesses in {len(_files(scan, CW))} files: {_rule_counts(scan, CW)}.{_on_sample(scan, SAMPLE_WEAK)}
Each is a text match to be confirmed by the system owner (Appendix B).

**Criteria.** TLS 1.0 and 1.1 are formally deprecated {cite("RFC-8996")}. NIST's draft
SP 800-131A Rev. 3 (draft) treats 3DES as disallowed and retires SHA-1 after 2030
{cite("NIST-800-131A", "NIST-SHA1")}. PCI DSS Requirement 12.3.3 calls for an inventory
of cipher suites and protocols reviewed at least every 12 months (text as reproduced in
the PCI SSC's official SAQ D) {cite("PCI-12-3-3")}.

**Cause.** Likely cause, to be confirmed: legacy partner integrations pinned old protocol
versions, and no control flags deprecated algorithms before deployment.

**Effect.** Known attacks on these algorithms and protocols do not need a quantum
computer. A migration to post-quantum cryptography built on top of them would inherit
their weaknesses.

**Recommendation.**
1. Confirm each Appendix B item with its owner, then plan removal of confirmed weak
   usages, prioritizing anything on an internet-facing path.
2. Add a check (this scanner or an equivalent, after validating it on the organization's
   own files) to the build pipeline so new weak usages are flagged before release.

**Management response.** *[To be completed by the process owner.]*
"""


def finding_3(scan: ScanResult, kem: list[KemProfile]) -> str:
    wire, ms = hybrid_cost(kem)
    return f"""
## Finding 3: {TITLES[2]}

**Rating: {rating_3(scan)}**

**Condition.** The scanner reported {scan.by_category()[QV]} quantum-vulnerable usages in
{len(_files(scan, QV))} files: {_rule_counts(scan, QV)}. Algorithm names and key sizes
appear directly in code and configuration{_on_sample(scan, SAMPLE_HARDCODED)}, rather
than being selected from a central policy.

**Criteria.** NIST IR 8547 (draft) proposes deprecating these algorithms after 2030 and
disallowing them after 2035 {cite("NIST-IR-8547")}. For high-value and high-impact US
federal systems, EO 14412 sets 31 December 2030 for PQC key establishment
{cite("EO-14412")}, and the UK NCSC sets 2031 for priority migration
{cite("NCSC-TIMELINE")}. These bind their own audiences, not a private finance function,
but they indicate the pace regulators expect. The replacement algorithms are standardized
{cite("FIPS-203", "FIPS-204-205")}, and hybrid TLS key exchange is published
{cite("RFC-10024")}.

**Cause.** Likely cause, to be confirmed: cryptography was treated as a fixed
implementation detail, so an algorithm change means editing every location.

**Effect.** Each hardcoded location lengthens the migration time *y*, which directly
worsens Finding 1. A larger RSA key is not a substitute: in the textbook circuit the
qubit count grows as 2n + 3 {cite("BEAUREGARD-2003")}, so RSA-4096 roughly doubles what an
attack needs, which raises the bar without removing the threat.

**Recommendation.**
1. Centralize algorithm selection in configuration or a shared library.
2. Migrate key exchange first, since it is what harvest-now, decrypt-later targets, using
   the hybrid. Measured on one workstation: {wire:,} bytes and about {ms:.1f} ms per
   handshake (Appendix C).
3. Plan signatures (RSA, ECDSA, DSA) to move to ML-DSA on the NIST schedule.

**Management response.** *[To be completed by the process owner.]*
"""


def finding_4(scan: ScanResult) -> str:
    return f"""
## Finding 4: {TITLES[3]}

**Rating: {RATING_4}**

**Condition.** No inventory of cryptographic algorithms, protocols and keys existed before
this assessment (a scenario assumption for the fictional organization). The inventory
produced here covers {scan.files_scanned} files of code and configuration only. It does
not cover vendor products, SaaS systems, cloud-service defaults, hardware security modules
or live network endpoints.

**Criteria.** PCI DSS Requirement 12.3.3 calls for an up-to-date inventory of cipher
suites and protocols, reviewed at least every 12 months, with a documented strategy for
responding to changes in cryptographic vulnerabilities {cite("PCI-12-3-3")}. The G7 Cyber
Expert Group encourages financial institutions to plan for quantum risk
{cite("G7-CEG-2024")}, and the UK NCSC sets 2028 for completing discovery
{cite("NCSC-TIMELINE")}.

**Cause.** Likely cause, to be confirmed: ownership of cryptography is spread across
application, infrastructure and vendor-management teams, with no single owner.

**Effect.** Without an inventory, the migration cannot be sized, prioritized by data
lifetime (Finding 1) or evidenced to examiners and auditors.

**Recommendation.**
1. Assign an owner for the cryptographic inventory.
2. Extend the code inventory with network scanning of TLS endpoints and with the vendor
   review in Finding 5.
3. Review the inventory at least annually, alongside the Finding 1 analysis.

**Management response.** *[To be completed by the process owner.]*
"""


# ---- Finding 5: vendors ---------------------------------------------------------------
def _vendor_table(ratings: list[Rating]) -> str:
    rows = "\n".join(
        f"| {r.vendor.name} | {', '.join(d.name for d in r.vendor.data_types)} | "
        f"{'yes' if r.vendor.responded else 'no'} | **{r.level}** | {r.reason}"
        f"{'; historic exposure: ' + ', '.join(r.historic) if r.historic else ''} | "
        f"{r.vendor.follow_up.isoformat()} |" for r in ratings)
    return ("| Vendor | Data handled | Responded | Rating | Basis of rating | Follow-up |\n"
            "|---|---|---|---|---|---|\n" + rows)


def finding_5(cfg: Config, ratings: list[Rating], tracker_name: str) -> str:
    levels = Counter(r.level for r in ratings)
    rating = vendor_finding_rating(ratings)
    return f"""
## Finding 5: {TITLES[4]}

**Rating: {rating}**

**Condition.** {len(ratings)} vendors that handle the organization's data were sent the
vendor PQC questionnaire (`vendor_pqc_questionnaire.md`); answers are recorded in
`{tracker_name}`. Ratings are computed from the recorded answers by a stated rule
(`vendor_review.py`): a vendor is rated on the exposure ITS stated timeline would add,
using the Mosca inputs across all three planning scenarios. Result: {levels['High']} High,
{levels['Medium']} Medium, {levels['Low']} Low. The tracker records the vendors' own
statements; it holds no evidence, so every answer is a claim until evidenced.

{_vendor_table(ratings)}

"Historic exposure" marks data that would be exposed even if the vendor migrated
instantly; it is real, but no vendor timeline can reduce it, so it does not drive the
rating.

**Criteria.** CISA, NSA and NIST encourage organizations to engage their technology
vendors about their quantum-readiness roadmaps and to plan for contract changes
{cite("CISA-VENDORS")}. The G7 Cyber Expert Group suggests financial entities reach out
to vendors and inventory quantum-vulnerable technology at key third parties
{cite("G7-CEG-2024-THIRD-PARTIES")}, and the UK NCSC's 2028 discovery milestone includes
supplier dependencies {cite("NCSC-TIMELINE")}. EO 14412 directs a proposed rule that would
require covered US federal contractors to meet PQC standards by 2030 {cite("EO-14412")}.

**Cause.** Likely cause, to be confirmed: vendor due diligence has not included
cryptography-migration questions, so vendor timelines were unknown.

**Effect.** Data held or transmitted by vendors could be recorded and later decrypted
outside the organization's visibility. The code scanner cannot see it (see Limitations).

**Recommendation.**
1. Send the questionnaire to every vendor that handles a data type in Finding 1.
2. Follow up High-rated vendors by the dates shown, and request evidence (Q12) from all.
3. Add PQC timeline and notification clauses to contracts at renewal.
4. Re-rate vendors whenever their answers change; the rating is only as current as the
   tracker.

**Management response.** *[To be completed by the process owner.]*
"""


def observation(scan: ScanResult) -> str:
    files = _files(scan, PQ)
    if not files or scan.root_label != SAMPLE_CODEBASE.name:
        return ""
    return f"""
## Positive observation

The scanner reported {scan.by_category()[PQ]} PQC-ready usages in {", ".join(f"`{f}`" for f in files)}:
per its configuration, the closed-period archive uses hybrid X25519MLKEM768 key exchange,
ML-KEM-768, ML-DSA-65 signatures, AES-256-GCM and SHA-384. If confirmed in operation, it
could serve as the reference pattern for other systems.
"""
