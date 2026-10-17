"""Finding 6 and Appendix E of pqc_readiness_report.md: what the TLS check observed.

Finding 6 compares each vendor's recorded hybrid key exchange answer with the handshakes
tls_check.py observed at the vendor's listed hosts. With no vendor observations loaded it
is rated "Not assessed": an absent check is reported as absent, never as a pass.

Appendix E reports the public-endpoint demo as recorded, in neutral terms: what each
endpoint negotiated at one moment, with no assessment of the organizations behind them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from report_findings import cite
from tls_check import OFFERED_GROUPS, Observation
from vendor_observed import (CONSISTENT, CONTRADICTED, NO_CLAIM, NOT_OBSERVED, TITLE,
                             UNCLAIMED, VendorClaim, claims_rating)


@dataclass(frozen=True)
class TlsEvidence:
    """What the report needs from Part 9."""
    claims: tuple[VendorClaim, ...]
    demo: tuple[Observation, ...] | None     # None when the demo evidence is not current
    source: str                              # vendor observations file, or "" if none
    delay: int                               # seconds between demo handshakes


def claims_summary(claims: Sequence[VendorClaim]) -> str:
    """One sentence for the executive summary."""
    observed = [c for c in claims if c.outcome != NOT_OBSERVED]
    if not observed:
        return ("No vendor endpoint was checked in this run, so recorded hybrid claims "
                "remain uncorroborated")
    bad = sum(c.outcome == CONTRADICTED for c in observed)
    return (f"{bad} of {len(observed)} checked vendors had a hybrid claim contradicted by "
            "an observed handshake")


def _claims_table(claims: Sequence[VendorClaim]) -> str:
    rows = "\n".join(f"| {c.vendor} | {c.claimed} | {c.observed} | "
                     f"{', '.join(f'`{h}`' for h in c.hosts) or 'none listed'} | "
                     f"{c.observed_at or 'n/a'} | {c.outcome} |" for c in claims)
    return ("| Vendor | Hybrid claimed (Q5) | Hybrid observed | Hosts checked | Observed (UTC) "
            "| Check |\n|---|---|---|---|---|---|\n" + rows)


def _condition(claims: Sequence[VendorClaim], source: str) -> str:
    observed = [c for c in claims if c.outcome != NOT_OBSERVED]
    if not observed:
        return ("No vendor endpoint was checked in this run. Real vendor hostnames are kept "
                "out of the repository (in the gitignored `vendor_hosts.local.yaml`), and the "
                f"{len(claims)} vendors here are fictional, so none is mapped to a real site. "
                "The comparison rule is exercised on fixture observations in the self-checks, "
                "and Appendix E shows the handshake check on public endpoints.")
    n = Counter(c.outcome for c in observed)
    return (f"{len(observed)} of {len(claims)} vendors had their listed hosts checked with "
            f"one TLS handshake each (`{source}`). Result: {n[CONTRADICTED]} hybrid claims "
            f"contradicted, {n[UNCLAIMED]} vendors observed hybrid without claiming it, "
            f"{n[CONSISTENT]} consistent, {n[NO_CLAIM]} with no claim to compare. The check "
            "sees only the listed public endpoints (see Limitations).")


def finding_6(claims: Sequence[VendorClaim], source: str) -> str:
    return f"""
## Finding 6: {TITLE}

**Rating: {claims_rating(claims)}**

**Condition.** {_condition(claims, source)}

{_claims_table(claims)}

**Criteria.** Hybrid key exchange in TLS 1.3 is visible from outside: the server's chosen
group appears in the handshake {cite("RFC-8446", "RFC-10024")}. CISA, NSA and NIST encourage
organizations to engage their technology vendors about quantum-readiness roadmaps
{cite("CISA-VENDORS")}, and the G7 Cyber Expert Group suggests financial entities
inventory quantum-vulnerable technology at key third parties
{cite("G7-CEG-2024-THIRD-PARTIES")}. A vendor's statement is a claim until evidenced; the
questionnaire asks for evidence (Q12).

**Cause.** Likely cause, to be confirmed: vendor answers have been recorded without
independent corroboration, and the endpoints that carry the organization's data have not
been listed for checking.

**Effect.** A vendor rated on a hybrid claim (Finding 5) may in fact negotiate classical
key exchange on connections carrying the data, so its harvest-now, decrypt-later
exposure would be understated. Where hybrid is observed but not claimed, the tracker
understates the vendor.

**Recommendation.**
1. List each vendor's data-carrying hostnames in `vendor_hosts.local.yaml` (never
   committed), run `py tls_check.py --vendors`, and rerun the assessment with
   `PQC_VENDOR_OBSERVATIONS=vendor_observations.local.json`. That run writes its report
   and log to gitignored `*.local.*` files, because they name the vendors' hosts.
2. For each contradicted claim, ask the vendor which endpoints the claim covers, and rate
   the vendor as if `hybrid_in_transit` were "no" until it is evidenced.
3. Update the tracker where hybrid is observed but not claimed.
4. Treat a consistent result as one piece of evidence about one endpoint, not proof
   about the vendor's environment.

**Management response.** *[To be completed by the process owner.]*
"""


def appendix_e(demo: Sequence[Observation] | None, delay: int) -> str:
    heading = "\n## Appendix E: TLS key exchange observed at public endpoints (demo)\n\n"
    if not demo:
        return heading + ("Not included in this run: `tls_demo_observations.json` is absent "
                          "or out of date (see the self-checks). Run `py tls_check.py`.\n")
    rows = "\n".join(
        f"| `{o.host}` | {o.tls_version} | {o.key_exchange_group or 'not shown'} | "
        f"{o.key_exchange_class} | {o.cert_key_type} {o.cert_key_bits or ''} | "
        f"{o.cert_signature_algorithm} | {o.observed_at} |" if o.status == "ok" else
        f"| `{o.host}` | handshake failed: {o.error} | | | | | {o.observed_at} |"
        for o in demo)
    return heading + f"""A demonstration of `tls_check.py` on the public endpoints listed in
`tls_hosts.yaml`; none belongs to the organization or its vendors. Each host received one
ordinary TLS handshake on port 443, {delay} seconds apart, offering `{OFFERED_GROUPS}`
(the hybrid first) {cite("RFC-10024")}; nothing else was sent. Each row records what the
endpoint that answered (often a CDN) negotiated at that moment, as observed with
{demo[0].openssl_version.split(' (')[0]}. It is not an assessment of any organization.
Evidence per row, including the certificate's SHA-256, is in `tls_demo_observations.json`.

| Host | TLS | Key exchange group | Class | Certificate key | Certificate signature | Observed (UTC) |
|---|---|---|---|---|---|---|
{rows}
"""
