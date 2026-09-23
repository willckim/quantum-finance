"""Part 6 -- Vendor PQC review: the cryptography a code scanner cannot see.

WHY THIS PART EXISTS
--------------------
The scanner (Part 3) only reads the code and configuration in one folder. Much of a
finance function's sensitive data lives with vendors instead: payroll providers, card
processors, virtual data rooms, tax software, banking portals. Their cryptography is
invisible to any scan the organization can run, so the only way to assess it is to ask,
record the answers and follow up. This module provides the three pieces for that:

  1. QUESTIONNAIRE  a standard set of questions to send to each vendor
                    (written to vendor_pqc_questionnaire.md)
  2. TRACKER        a CSV with one row per vendor: data handled, response, roadmap,
                    target date, follow-up date (vendor_tracker_template.csv is blank;
                    sample_vendor_tracker.csv holds five fictional vendors)
  3. RATING         a risk rating per vendor, COMPUTED from the tracker by a stated rule,
                    never typed in, so two reviewers get the same answer

THE RATING RULE (Mosca's inequality, applied to one vendor)
-----------------------------------------------------------
A vendor's stated completion year sets its own migration time y_v. Data the vendor holds
is put at risk BY THE VENDOR when it would be safe with an immediate migration but is
exposed at the vendor's pace:

    vendor-attributable exposure in a scenario:   x + y_v > z   but   x <= z

  High    no response, no dated roadmap, or vendor-attributable exposure in the Base or
          Optimistic scenario (exposure that persists even if the CRQC is late)
  Medium  vendor-attributable exposure only in the Pessimistic scenario
  Low     the vendor's timeline adds no exposure in any scenario

A vendor already running hybrid key exchange for connections carrying the data gets
y_v = 0. Data exposed even with an instant migration (x > z, such as long-lived M&A
documents) is noted as HISTORIC exposure: it is real, but no vendor timeline can fix it,
so it does not drive the vendor's rating.

A tracker records what vendors SAY. Answers are claims until evidenced (Q12 asks for
evidence), and the report treats them that way.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from common import HERE, Check, not_run, section
from config import Config, DataType, matches_shipped_defaults
from mosca import EXPOSED_NOW, SAFE, assess

QUESTIONNAIRE_MD = HERE / "vendor_pqc_questionnaire.md"
TRACKER_TEMPLATE = HERE / "vendor_tracker_template.csv"
SAMPLE_TRACKER = HERE / "sample_vendor_tracker.csv"

YES_NO = ("yes", "no", "unknown")
COLUMNS = ("vendor", "service", "data_types_handled", "questionnaire_sent",
           "response_received", "pqc_roadmap", "algorithms_stated", "hybrid_in_transit",
           "pqc_target_year", "follow_up_date", "notes")


@dataclass(frozen=True)
class Question:
    qid: str
    topic: str
    text: str
    strong_answer: str


QUESTIONS: tuple[Question, ...] = (
    Question("Q1", "roadmap", "Do you have a documented post-quantum cryptography (PQC) "
             "migration roadmap? Please share it or a summary.",
             "A dated plan with an accountable owner, covering products and infrastructure."),
    Question("Q2", "inventory", "Have you inventoried where your product and infrastructure "
             "use public-key cryptography (RSA, elliptic-curve, Diffie-Hellman)?",
             "A maintained inventory, reviewed at least annually."),
    Question("Q3", "algorithms_standards", "Which post-quantum algorithms and standards do you "
             "use or plan to use (for example ML-KEM / FIPS 203, ML-DSA / FIPS 204, SLH-DSA / "
             "FIPS 205)? Are the implementations validated under FIPS 140-3?",
             "NIST-standardized algorithms, named, with validated implementations."),
    Question("Q4", "target_dates", "By what dates will (a) key exchange and (b) digital "
             "signatures protecting our data use post-quantum algorithms?",
             "Specific years for each, with key exchange first."),
    Question("Q5", "hybrid", "Do you support hybrid key exchange (classical plus post-quantum, "
             "for example X25519MLKEM768 per RFC 10024) on connections that carry our data? "
             "Since when, or by when?",
             "Enabled today, or a date, on every endpoint that carries our data."),
    Question("Q6", "in_transit", "How is our data protected in transit (protocols, TLS "
             "versions, key-exchange groups), across web, API, file transfer and email?",
             "TLS 1.3 everywhere, with hybrid groups where supported; no legacy protocols."),
    Question("Q7", "at_rest", "How is our data protected at rest: which algorithm and key "
             "length, and how are data-encryption keys wrapped (RSA, or a symmetric key "
             "service)?",
             "AES-256 data keys wrapped by a symmetric key service, or a PQC wrap plan."),
    Question("Q8", "keys_certificates", "Who manages the keys, and what is your plan for "
             "post-quantum certificates and PKI?",
             "Named key owner, HSM-backed keys, and a certificate migration plan."),
    Question("Q9", "agility", "Can you change cryptographic algorithms by configuration, "
             "without a new product release?",
             "Yes, with a tested procedure."),
    Question("Q10", "subprocessors", "Do your subprocessors (cloud, CDN, HSM and other "
             "providers) have PQC roadmaps, and how do you track them?",
             "Tracked in your own vendor management, with dates."),
    Question("Q11", "legacy", "Do any connections carrying our data still accept TLS 1.0 or "
             "1.1, SHA-1 or 3DES? If so, when will they be removed?",
             "No; or a removal date within 90 days."),
    Question("Q12", "commitment_evidence", "Will you commit to these timelines in our "
             "contract and notify us of changes? What evidence can you provide (test "
             "reports, attestations, independent assessments)?",
             "Contractual commitment plus third-party evidence."),
)

# Topics the questionnaire must always cover (the minimum a reviewer needs).
REQUIRED_TOPICS = ("roadmap", "algorithms_standards", "target_dates", "hybrid",
                   "in_transit", "at_rest")


def questionnaire_markdown() -> str:
    rows = "\n".join(f"| {q.qid} | {q.text} | {q.strong_answer} |" for q in QUESTIONS)
    return f"""# Vendor Post-Quantum Cryptography Questionnaire

Send to every vendor that stores, processes or transmits the organization's sensitive
data. Record the answers in the vendor tracker (`vendor_tracker_template.csv`); the
tracker columns map to Q1 (pqc_roadmap), Q3 (algorithms_stated), Q4 (pqc_target_year)
and Q5 (hybrid_in_transit).

**Why we are asking.** Data encrypted with today's public-key cryptography (RSA and
elliptic-curve) can be recorded now and decrypted once a cryptographically relevant
quantum computer exists ("harvest now, decrypt later"). NIST published the replacement
standards in August 2024 (FIPS 203, 204 and 205). We are asking each vendor how and when
the data it holds for us will be protected by them.

| # | Question | What a strong answer includes |
|---|---|---|
{rows}

Answers are recorded as the vendor's statements. Where possible, please attach evidence
(Q12).
"""


# ---- tracker ---------------------------------------------------------------------------
class TrackerError(ValueError):
    """A vendor tracker row is missing a field or holds an invalid value."""


@dataclass(frozen=True)
class Vendor:
    name: str
    service: str
    data_types: tuple[DataType, ...]
    responded: bool
    roadmap: str
    algorithms: str
    hybrid_in_transit: str
    target_year: int | None
    follow_up: date
    notes: str


def _v(row: dict, key: str) -> str:
    """A cell as text. Excel drops trailing empty cells, which DictReader reports as None."""
    return (row.get(key) or "").strip()


def _choice(row: dict, key: str, where: str) -> str:
    value = _v(row, key).lower()
    if value not in YES_NO:
        raise TrackerError(f"{where}: {key} must be one of {YES_NO}, got {_v(row, key)!r}")
    return value


def _date(row: dict, key: str, where: str) -> date:
    try:
        return date.fromisoformat(_v(row, key))
    except ValueError as exc:
        raise TrackerError(f"{where}: {key} must be a YYYY-MM-DD date ({exc})") from exc


def _row(row: dict, n: int, cfg: Config) -> Vendor:
    where = f"row {n} ({_v(row, 'vendor') or '?'})"
    if not _v(row, "vendor"):
        raise TrackerError(f"{where}: vendor name is required")
    known = {d.name: d for d in cfg.data_types}
    names = [s.strip() for s in _v(row, "data_types_handled").split(";") if s.strip()]
    unknown = [s for s in names if s not in known]
    if not names or unknown:
        raise TrackerError(f"{where}: data_types_handled must list data types from the "
                           f"config, separated by ';' (unknown: {unknown})")
    target = _v(row, "pqc_target_year")
    if target and not (target.isdigit() and cfg.as_of_year <= int(target) <= 2100):
        raise TrackerError(f"{where}: pqc_target_year must be blank or a year from "
                           f"{cfg.as_of_year}, got {target!r}")
    sent, follow_up = _date(row, "questionnaire_sent", where), _date(row, "follow_up_date", where)
    if follow_up < sent:
        raise TrackerError(f"{where}: follow_up_date is before questionnaire_sent")
    responded = _choice(row, "response_received", where) == "yes"
    roadmap, hybrid = _choice(row, "pqc_roadmap", where), _choice(row, "hybrid_in_transit", where)
    if not responded and ("yes" in (roadmap, hybrid) or target):
        raise TrackerError(f"{where}: answers are recorded but response_received is 'no'")
    return Vendor(_v(row, "vendor"), _v(row, "service"), tuple(known[s] for s in names),
                  responded, roadmap, _v(row, "algorithms_stated"), hybrid,
                  int(target) if target else None, follow_up, _v(row, "notes"))


def parse_tracker(text: str, cfg: Config) -> list[Vendor]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise TrackerError(f"tracker: missing column(s) {missing}")
    vendors = [_row(row, n, cfg) for n, row in enumerate(reader, start=2)
               if any((value or "").strip() for value in row.values())]     # skip blank rows
    if not vendors:
        raise TrackerError("tracker: no vendor rows")
    names = [v.name.casefold() for v in vendors]
    if len(set(names)) != len(names):
        raise TrackerError("tracker: vendor names must be unique")
    return vendors


def load_tracker(path: Path, cfg: Config) -> list[Vendor]:
    return parse_tracker(path.read_text(encoding="utf-8-sig"), cfg)   # Excel adds a BOM


# ---- rating ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Rating:
    vendor: Vendor
    level: str                      # High | Medium | Low
    reason: str
    historic: tuple[str, ...]       # data types exposed even with an instant migration


def _completion_year(v: Vendor, as_of: int) -> int | None:
    if v.hybrid_in_transit == "yes":
        return as_of
    return v.target_year if v.roadmap == "yes" else None


def _attributable(v: Vendor, cfg: Config, y_v: int) -> dict[str, list[str]]:
    """Per scenario: data types exposed at the vendor's pace but safe with y = 0."""
    as_of = cfg.as_of_year
    return {s.name: [d.name for d in v.data_types
                     if assess(d, s, y_v, as_of).status != SAFE
                     and assess(d, s, 0, as_of).status == SAFE] for s in cfg.scenarios}


def rate(v: Vendor, cfg: Config) -> Rating:
    as_of = cfg.as_of_year
    base = next(s for s in cfg.scenarios if s.name == "Base")
    historic = tuple(d.name for d in v.data_types
                     if assess(d, base, 0, as_of).status == EXPOSED_NOW)
    if not v.responded:
        return Rating(v, "High", "no response to the questionnaire", historic)
    done = _completion_year(v, as_of)
    if done is None:
        return Rating(v, "High", "no dated PQC roadmap", historic)
    hits = _attributable(v, cfg, done - as_of)
    pace = "hybrid in transit today" if done == as_of else f"PQC by {done}"
    severe = [(s, hits[s]) for s in ("Base", "Optimistic") if hits[s]]
    if severe:
        scenario, names = severe[0]
        return Rating(v, "High", f"{pace} leaves {', '.join(names)} exposed in the "
                      f"{scenario} scenario", historic)
    if hits["Pessimistic"]:
        return Rating(v, "Medium", f"{pace} leaves {', '.join(hits['Pessimistic'])} exposed "
                      "only in the Pessimistic scenario", historic)
    return Rating(v, "Low", f"{pace} adds no exposure in any scenario", historic)


# ======================================================================================
# THE DEMO AND ITS SELF-CHECKS
# ======================================================================================
# Expected ratings for the five fictional vendors, worked out BY HAND from the default
# config (as of 2026; planning z = 5 / 9 / 14; shelf lives from pqc_config.yaml):
#   A payroll: PQC 2028 (y=2). Payroll x=4: 6 > 5 but 4 <= 5 -> Pessimistic only.
#              Employee PII x=6 > 5 is historic in Pessimistic, not the vendor's.  Medium
#   B storage: hybrid today (y=0) -> adds nothing.                                Low
#              M&A x=18 > 9 is historic. (A rule that counted ANY exposure would
#              rate B High; this row is what tells the two rules apart.)
#   C data room: PQC 2032 (y=6). Contracts x=4: 10 > 9 but 4 <= 9 -> Base.          High
#              M&A x=18 > 9: historic.
#   D tax:     no response.                                                         High
#   E banking: PQC 2030 (y=4). Credentials x=2: 6 > 5, 6 <= 9 -> Pessimistic only.  Medium
SAMPLE_EXPECTED = {"Vendor A": "Medium", "Vendor B": "Low", "Vendor C": "High",
                   "Vendor D": "High", "Vendor E": "Medium"}
SAMPLE_HISTORIC = {"Vendor B": ("M&A documents",), "Vendor C": ("M&A documents",)}


def _rows(**changes: str) -> str:
    """The sample header plus Vendor A's row, with text replacements applied."""
    header, first = SAMPLE_TRACKER.read_text(encoding="utf-8").splitlines()[:2]
    for old, new in changes.values():
        first = first.replace(old, new, 1)
    return header + "\n" + first


BROKEN_ROWS = (
    ("unknown data type", {"a": ("Payroll records", "Payrol records")}),
    ("bad follow-up date", {"a": ("2026-10-15", "15/10/2026")}),
    ("bad questionnaire_sent date", {"a": ("2026-09-01", "Sept 1")}),
    ("bad yes/no value", {"a": (",yes,", ",maybe,")}),
    ("follow-up before sent", {"a": ("2026-10-15", "2026-08-01")}),
    ("answers without a response", {"a": (",yes,yes,", ",no,yes,")}),
    ("target year not a year", {"a": (",2028,", ",2O28,")}),
)


def _tracker_checks(cfg: Config) -> list[Check]:
    header, first = SAMPLE_TRACKER.read_text(encoding="utf-8").splitlines()[:2]
    broken = [(label, _rows(**chg)) for label, chg in BROKEN_ROWS] + [
        ("missing column", header.replace(",notes", "") + "\n" + first),
        ("duplicate vendor", header + "\n" + first + "\n" + first)]
    accepted = []
    for label, text in broken:
        try:
            parse_tracker(text, cfg)
            accepted.append(label)
        except TrackerError:
            pass
    valid = {"Excel byte-order mark": "﻿" + header + "\n" + first,
             "blank trailing line": header + "\n" + first + "\n,,,,,,,,,,\n",
             "short row (trailing cells dropped)": header + "\n" + first.rsplit(",", 1)[0]}
    refused = []
    for label, text in valid.items():
        try:
            parse_tracker(text, cfg)
        except TrackerError as exc:
            refused.append(f"{label} ({exc})")
    template = TRACKER_TEMPLATE.read_text(encoding="utf-8").splitlines()
    return [
        Check("Vendor: tracker template has exactly the required columns and no data rows",
              template == [",".join(COLUMNS)], f"{len(COLUMNS)} columns"),
        Check("Vendor: invalid tracker rows are rejected",
              not accepted, "accepted: " + ", ".join(accepted) if accepted
              else f"{len(broken)}/{len(broken)} rejected"),
        Check("Vendor: valid Excel-style trackers (BOM, blank line, short row) are accepted",
              not refused, "; ".join(refused) if refused else f"{len(valid)}/{len(valid)}"),
    ]


def _fixture(cfg: Config, data, **fields) -> Vendor:
    base = dict(name="fixture", service="", data_types=data, responded=True, roadmap="no",
                algorithms="", hybrid_in_transit="no", target_year=None,
                follow_up=date(cfg.as_of_year, 12, 31), notes="")
    return Vendor(**{**base, **fields})


def _rule_checks(cfg: Config) -> list[Check]:
    """Each branch of the rating rule on a vendor built to reach it (answers by hand)."""
    first = cfg.data_types[:1]
    # A 12-year shelf life: historic in Base (12 > 9), but in Optimistic 12 <= 14 while
    # 12 + 6 > 14, so a 2032 vendor causes exposure there. Must not read as Low.
    late = (DataType("fixture-12y", 12, "fixture", "regulation", ()),)
    cases = {
        "no response -> High, even with a roadmap on file":
            (_fixture(cfg, first, responded=False, roadmap="yes", target_year=2027), "High"),
        "no dated roadmap -> High": (_fixture(cfg, first), "High"),
        "hybrid in transit, no target year -> Low":
            (_fixture(cfg, first, hybrid_in_transit="yes"), "Low"),
        "exposure it causes only in Optimistic -> High":
            (_fixture(cfg, late, roadmap="yes", target_year=2032), "High"),
    }
    wrong = [f"{label} (got {rate(v, cfg).level})" for label, (v, want) in cases.items()
             if rate(v, cfg).level != want]
    return [Check("Vendor: every branch of the rating rule gives its hand-worked level",
                  not wrong, "; ".join(wrong) if wrong else f"{len(cases)} branches")]


def vendor_checks(cfg: Config, ratings: list[Rating]) -> list[Check]:
    covered = {q.topic for q in QUESTIONS}
    md = QUESTIONNAIRE_MD.read_text(encoding="utf-8") if QUESTIONNAIRE_MD.is_file() else ""
    got = {r.vendor.name.split(" (")[0]: r.level for r in ratings}
    historic = {r.vendor.name.split(" (")[0]: r.historic for r in ratings if r.historic}
    return [
        Check("Vendor: questionnaire covers every required topic and is written in full",
              set(REQUIRED_TOPICS) <= covered and all(q.text in md for q in QUESTIONS),
              f"{len(QUESTIONS)} questions; missing topics "
              f"{sorted(set(REQUIRED_TOPICS) - covered) or 'none'}"),
        Check("Vendor: ratings for the 5 fictional vendors match the hand-worked answers",
              got == SAMPLE_EXPECTED, f"{got}"),
        Check("Vendor: historic exposure is reported for exactly the hand-worked vendors",
              historic == SAMPLE_HISTORIC, f"{historic}"),
    ] + _rule_checks(cfg) + _tracker_checks(cfg)


def part6_vendors(cfg: Config, tracker: Path = SAMPLE_TRACKER) -> tuple[list[Rating],
                                                                       list[Check]]:
    section("PART 6 -- VENDOR PQC REVIEW (what the scanner cannot see)")
    QUESTIONNAIRE_MD.write_text(questionnaire_markdown(), encoding="utf-8")
    ratings = [rate(v, cfg) for v in load_tracker(tracker, cfg)]
    sample = tracker == SAMPLE_TRACKER
    print(f"  Questionnaire: {len(QUESTIONS)} questions -> {QUESTIONNAIRE_MD.name}")
    print(f"  Tracker: {tracker.name} ({len(ratings)} vendors"
          f"{', all fictional' if sample else ''})\n")
    for r in ratings:
        historic = f"; historic: {', '.join(r.historic)}" if r.historic else ""
        print(f"    {r.level:<7} {r.vendor.name:<40} {r.reason}{historic}")
    if sample and matches_shipped_defaults(cfg):
        return ratings, vendor_checks(cfg, ratings)
    reason = "a custom tracker is loaded" if not sample else "the config differs from defaults"
    return ratings, [not_run("Vendor: hand-worked sample checks", reason)] + (
        _rule_checks(cfg) + _tracker_checks(cfg) if sample else [])
