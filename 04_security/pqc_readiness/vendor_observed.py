"""Part 9b -- Vendor claims against observed handshakes.

Questionnaire Q5 asks each vendor whether the connections carrying the organization's
data use hybrid key exchange; the tracker records the answer in hybrid_in_transit.
tls_check.py records what the vendor's listed hosts actually negotiated. This module puts
the two side by side, in the tracker's hybrid_observed column, and flags a mismatch
either way:

    claimed hybrid, observed classical   the evidence contradicts the claim
    observed hybrid, not claimed         the record understates the vendor (update it)

OBSERVED VALUE PER VENDOR (completed handshakes with a recognized group only)
    yes           every one used a hybrid (or ML-KEM-only) group
    no            every one used a classical group
    partial       some did and some did not
    not observed  no host listed, or no handshake completed with a recognized group
A failed handshake or an unrecognized group never counts as classical.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Sequence

from common import Check
from register_outputs import csv_safe
from tls_check import (CLASSICAL, HYBRID, OFFERED_GROUPS, PQC_ONLY, QUANTUM_SAFE, UNKNOWN,
                       Observation, ObservationsError, load_observations)
from vendor_review import COLUMNS, OBSERVED_COLUMN, SAMPLE_TRACKER, Vendor

YES, NO, PARTIAL, NOT_OBSERVED = "yes", "no", "partial", "not observed"
CONSISTENT = "consistent"
CONTRADICTED = "mismatch: claimed hybrid, observed classical"
UNCLAIMED = "mismatch: observed hybrid, not claimed"
NO_CLAIM = "no claim to compare"
EVIDENCE_COLUMNS = ("observed_hosts", "observed_at", "claim_check")
TITLE = "Vendor hybrid key exchange claims, checked against observed handshakes"


@dataclass(frozen=True)
class VendorClaim:
    vendor: str
    claimed: str                     # hybrid_in_transit as recorded: yes | no | unknown
    observed: str                    # yes | no | partial | not observed
    hosts: tuple[str, ...]
    observed_at: str
    outcome: str


def observed_value(observations: Sequence[Observation]) -> str:
    known = [o.key_exchange_class for o in observations
             if o.status == "ok" and o.key_exchange_class in (*QUANTUM_SAFE, CLASSICAL)]
    if not known:
        return NOT_OBSERVED
    safe = sum(c in QUANTUM_SAFE for c in known)
    return YES if safe == len(known) else NO if safe == 0 else PARTIAL


def claim_check(claimed: str, observed: str) -> str:
    if observed == NOT_OBSERVED:
        return NOT_OBSERVED
    if claimed == "yes":
        return CONSISTENT if observed == YES else CONTRADICTED
    if claimed == "no":
        return CONSISTENT if observed == NO else UNCLAIMED
    return NO_CLAIM


def compare(vendors: Sequence[Vendor], observations: Sequence[Observation],
            observe: Callable[[Sequence[Observation]], str] = observed_value) -> list[VendorClaim]:
    claims = []
    for v in vendors:
        mine = [o for o in observations if o.vendor.casefold() == v.name.casefold()]
        seen = observe(mine)
        times = sorted(o.observed_at for o in mine if o.observed_at)
        when = (times[0] if len(set(times)) <= 1 else f"{times[0]} to {times[-1]}") if times \
            else ""
        claims.append(VendorClaim(v.name, v.hybrid_in_transit, seen,
                                  tuple(o.host for o in mine), when,
                                  claim_check(v.hybrid_in_transit, seen)))
    return claims


def claims_rating(claims: Sequence[VendorClaim]) -> str:
    """Medium if any claim is contradicted; Low if vendors were observed and none is;
    Not assessed when no vendor was observed (absence of evidence is not a pass)."""
    if not any(c.outcome != NOT_OBSERVED for c in claims):
        return "Not assessed"
    return "Medium" if any(c.outcome == CONTRADICTED for c in claims) else "Low"


def load_vendor_observations(path: Path, vendors: Sequence[Vendor]) -> list[Observation]:
    _, observations = load_observations(path)
    names = {v.name.casefold() for v in vendors}
    stray = sorted({o.vendor for o in observations if o.vendor.casefold() not in names})
    if stray:
        raise ObservationsError(f"{path.name}: vendor(s) {stray} are not in the tracker")
    # A server cannot choose a hybrid group it was never offered, so a row recorded
    # without the hybrid on offer would manufacture a "claimed hybrid, observed classical".
    unoffered = sorted({o.host for o in observations if o.offered_groups != OFFERED_GROUPS})
    if unoffered:
        raise ObservationsError(f"{path.name}: {unoffered} were not offered "
                                f"{OFFERED_GROUPS}; rerun tls_check.py --vendors")
    return observations


def write_observed_tracker(out: Path, tracker: Path, claims: Sequence[VendorClaim]) -> None:
    """The tracker as given, with hybrid_observed filled and its evidence beside it."""
    by_name = {c.vendor.casefold(): c for c in claims}
    reader = csv.DictReader(io.StringIO(tracker.read_text(encoding="utf-8-sig")))
    at = COLUMNS.index(OBSERVED_COLUMN) + 1
    header = [*COLUMNS[:at], *EVIDENCE_COLUMNS, *COLUMNS[at:]]
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in reader:
            claim = by_name.get((row.get("vendor") or "").strip().casefold())
            if claim is None:
                continue                                   # blank rows
            filled = {**row, OBSERVED_COLUMN: claim.observed,
                      "observed_hosts": "; ".join(claim.hosts),
                      "observed_at": claim.observed_at, "claim_check": claim.outcome}
            writer.writerow([csv_safe((filled.get(c) or "").strip()) for c in header])


# ======================================================================================
# SELF-CHECKS (expected outcomes worked out by hand from the rule in the docstring)
# ======================================================================================
FAILED = "failed"
CLAIM_CASES = (
    ("yes", (HYBRID,), YES, CONSISTENT),
    ("yes", (CLASSICAL,), NO, CONTRADICTED),
    ("yes", (HYBRID, CLASSICAL), PARTIAL, CONTRADICTED),
    ("no", (HYBRID,), YES, UNCLAIMED),
    ("no", (PQC_ONLY,), YES, UNCLAIMED),
    ("no", (CLASSICAL,), NO, CONSISTENT),
    ("unknown", (HYBRID,), YES, NO_CLAIM),
    ("yes", (), NOT_OBSERVED, NOT_OBSERVED),
    ("yes", (FAILED,), NOT_OBSERVED, NOT_OBSERVED),      # a failure is not "classical"
    ("no", (UNKNOWN,), NOT_OBSERVED, NOT_OBSERVED),      # nor is an unrecognized group
    ("yes", (HYBRID, FAILED), YES, CONSISTENT),
)
FIXTURE = Observation("T-fixture", "", "host.example", 443, "ok", "TLSv1.3",
                      "TLS_AES_256_GCM_SHA384", "", "", "EC secp256r1", 256,
                      "ecdsa-with-SHA256", "0" * 64, "", "2026-01-01T00:00:00+00:00",
                      OFFERED_GROUPS, "", "", "")


def fixture(vendor: str, kind: str, host: str = "host.example") -> Observation:
    if kind == FAILED:
        return replace(FIXTURE, vendor=vendor, host=host, status=FAILED, error="timed out")
    return replace(FIXTURE, vendor=vendor, host=host, key_exchange_class=kind)


def _vendor(name: str, claimed: str) -> Vendor:
    return Vendor(name, "", (), True, "yes", "", claimed, None, None, "")


def claim_mistakes(observe: Callable[[Sequence[Observation]], str]) -> list[str]:
    wrong = []
    for n, (claimed, kinds, want_seen, want_outcome) in enumerate(CLAIM_CASES):
        name = f"case {n + 1}"
        obs = [fixture(name, k, f"h{i}.example") for i, k in enumerate(kinds)]
        got = compare([_vendor(name, claimed)], obs, observe)[0]
        if (got.observed, got.outcome) != (want_seen, want_outcome):
            wrong.append(f"{name} claim={claimed} {list(kinds)}: {got.observed}/{got.outcome}")
    return wrong


def _failure_as_classical(observations: Sequence[Observation]) -> str:
    """The fault this guard exists for: anything not proven safe read as classical."""
    if not observations:
        return NOT_OBSERVED
    safe = [o.status == "ok" and o.key_exchange_class in QUANTUM_SAFE for o in observations]
    return YES if all(safe) else NO if not any(safe) else PARTIAL


def _tracker_output_check() -> Check:
    vendors = [_vendor("Vendor A (fictional payroll SaaS)", "no"),
               _vendor("Vendor B (fictional cloud file storage)", "yes")]
    claims = compare(vendors, [fixture(vendors[1].name, CLASSICAL, "files.vendor-b.invalid")])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "observed.csv"
        write_observed_tracker(out, SAMPLE_TRACKER, claims)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        header = out.read_text(encoding="utf-8").splitlines()[0].split(",")
    at = header.index("hybrid_in_transit")
    got = {r["vendor"].split(" (")[0]: (r[OBSERVED_COLUMN], r["claim_check"]) for r in rows}
    want = {"Vendor A": (NOT_OBSERVED, NOT_OBSERVED), "Vendor B": (NO, CONTRADICTED)}
    return Check("TLS: observed tracker puts hybrid_observed beside the vendor's answer and "
                 "flags the planted mismatch",
                 header[at + 1] == OBSERVED_COLUMN and got == want,
                 f"{got}" if got != want else f"{len(rows)} rows")


def _stray_vendor_check() -> Check:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "observations.json"
        rows = [asdict(fixture("Vendor Z", HYBRID))]
        path.write_text(json.dumps({"observations": rows}), encoding="utf-8")
        accepted = len(load_vendor_observations(path, [_vendor("Vendor Z", "yes")])) == 1
        refused = []
        for label, vendor, row in (
                ("vendor not in tracker", "Vendor Y", rows[0]),
                ("hybrid never offered", "Vendor Z", {**rows[0], "offered_groups": "X25519"})):
            path.write_text(json.dumps({"observations": [row]}), encoding="utf-8")
            try:
                load_vendor_observations(path, [_vendor(vendor, "yes")])
            except ObservationsError:
                refused.append(label)
    return Check("TLS: observations for a vendor not in the tracker, or taken without the "
                 "hybrid on offer, are refused; matching ones accepted",
                 accepted and len(refused) == 2, f"accepted {accepted}, refused {refused}")


def claim_checks() -> list[Check]:
    wrong = claim_mistakes(observed_value)
    caught = claim_mistakes(_failure_as_classical)
    ratings = {"none observed": ([], "Not assessed"),
               "only not observed": ([NOT_OBSERVED], "Not assessed"),
               "one contradicted": ([CONSISTENT, CONTRADICTED], "Medium"),
               "only unclaimed": ([UNCLAIMED, CONSISTENT], "Low")}
    bad = [label for label, (outcomes, want) in ratings.items()
           if claims_rating([VendorClaim("v", "", "", (), "", o) for o in outcomes]) != want]
    return [
        Check("TLS: claim vs observation gives the hand-worked outcome in every case",
              not wrong, "; ".join(wrong) if wrong else f"{len(CLAIM_CASES)} cases"),
        Check("TLS: the claim check catches a rule that reads failures as classical (control)",
              bool(caught), f"{len(caught)} cases differ"),
        Check("TLS: mismatch finding rating is computed (Not assessed / Medium / Low)",
              not bad, f"wrong: {bad}" if bad else f"{len(ratings)} cases"),
        _stray_vendor_check(),
        _tracker_output_check(),
    ]

