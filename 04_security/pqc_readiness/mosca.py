"""Part 2 -- Mosca's inequality: which finance data is ALREADY at risk?

THE INEQUALITY (Mosca, 2015)
----------------------------
    x = shelf life:      how long the data must stay confidential
    y = migration time:  how long it takes to move every system to post-quantum crypto
    z = threat horizon:  years until a cryptographically relevant quantum computer (CRQC)

    If  x + y > z,  the data is exposed.

Why the two lengths add: until the migration finishes (y years from now), new records are
still being sent and stored under RSA or elliptic-curve key exchange. The LAST record
protected the old way must then stay secret for x more years. An adversary who recorded
it ("harvest now, decrypt later") only has to wait for the quantum computer. If that
arrives before x + y years are up, the record is readable while it still matters.

Said out loud: "If the time our data has to stay secret plus the time it takes us to
switch encryption is longer than the time until a quantum computer can break today's
encryption, then data we are sending today is already compromised; it just has not been
read yet."

A second, starker number comes out of the same arithmetic. If x alone exceeds z, the data
is exposed even if the migration finished TODAY, because records already recorded under
RSA or ECC stay sensitive past the arrival date. The latest year the migration could have
finished and still protected that data type is z - x years from now; Assessment exposes
it as safe_by_year.

SHELF LIFE = RETENTION PERIOD (a deliberate lower bound)
--------------------------------------------------------
Each default shelf life is a legal RETENTION period, cited in sources.py, not a guess. A
retention period says how long a record must be KEPT; how long it must stay SECRET can be
longer (a Social Security number stays sensitive for life). Using retention periods
therefore understates exposure. The one type with no retention rule, bank credentials,
uses a NIST cryptoperiod instead and is labeled as an assumption.

THE THREE SCENARIOS (defined from the defender's point of view)
---------------------------------------------------------------
"Optimistic" means the quantum computer arrives LATE. The Global Risk Institute survey
uses the opposite convention (its "optimistic" respondents expect a CRQC SOONER), so its
labels are flipped here on purpose.

Each scenario is a WINDOW of years around a cited estimate, calculated at a stated
planning year inside it. They are planning scenarios, not predictions.

ALL INPUTS LIVE IN pqc_config.yaml (loaded and validated by config.py): the as-of year,
the migration time (default 10 years, the UK NCSC planning figure), every shelf life and
its source, and the three scenario windows. An organization replaces them there.
"""

from __future__ import annotations

from dataclasses import dataclass

from common import Check, not_run, section
from config import Config, DataType, Scenario, matches_shipped_defaults
# One spelling of each status, shared with the priority rules that test for them.
from priority_rules import EXPOSED, EXPOSED_NOW, SAFE  # noqa: F401  (re-exported)


@dataclass(frozen=True)
class Assessment:
    data: DataType
    scenario: Scenario
    x: int
    y: int
    z: int

    @property
    def exposure_years(self) -> int:
        """Years the data is still sensitive after the CRQC arrives (<= 0 means safe)."""
        return self.x + self.y - self.z

    @property
    def safe_by_year(self) -> int:
        """Latest year the migration could finish and still protect this data type."""
        return self.scenario.crqc_year - self.x

    @property
    def status(self) -> str:
        if self.x > self.z:
            return EXPOSED_NOW
        return EXPOSED if self.x + self.y > self.z else SAFE


def assess(data: DataType, scenario: Scenario, y: int, as_of: int) -> Assessment:
    return Assessment(data, scenario, data.shelf_life, y, scenario.crqc_year - as_of)


def assess_all(cfg: Config) -> dict[str, list[Assessment]]:
    return {s.name: [assess(d, s, cfg.migration_years, cfg.as_of_year) for d in cfg.data_types]
            for s in cfg.scenarios}


def _cell(a: Assessment) -> str:
    return a.status + (f" +{a.exposure_years}y" if a.status != SAFE else "")


def part2_mosca(cfg: Config) -> tuple[dict[str, list[Assessment]], list[Check]]:
    section("PART 2 -- MOSCA'S INEQUALITY: x (shelf life) + y (migration) > z (CRQC arrival)?")
    y, as_of = cfg.migration_years, cfg.as_of_year
    results = assess_all(cfg)
    print(f"  Inputs from {cfg.path_label}. As of {as_of}; migration time y = {y} years "
          f"(finishes {as_of + y}).\n  Planning scenarios (windows, not predictions):")
    for s in cfg.scenarios:
        print(f"    {s.label}, calculated at {s.crqc_year} (z = {s.crqc_year - as_of}): "
              f"{s.basis}.")
    header = "".join(f"{s.name + ' @' + str(s.crqc_year):>24}" for s in cfg.scenarios)
    print(f"\n  {'data type':<24} {'x':>3}  {header}")
    for i, d in enumerate(cfg.data_types):
        cells = "".join(f"{_cell(results[s.name][i]):>24}" for s in cfg.scenarios)
        print(f"  {d.name + (' *' if d.assumption else ''):<24} {d.shelf_life:>3}  {cells}")
    print("  (+Ny = years the data is still sensitive after the CRQC arrives; "
          "* = assumption, not a retention rule)")
    for s in cfg.scenarios:
        rows = results[s.name]
        exposed = sum(a.status != SAFE for a in rows)
        now = [a.data.name for a in rows if a.status == EXPOSED_NOW]
        print(f"\n  {s.name}: {exposed} of {len(rows)} data types exposed. "
              f"Exposed even if migration finished today: {', '.join(now) or 'none'}.")
    return results, _checks(cfg, results)


# ======================================================================================
# SELF-CHECKS
# ======================================================================================
# A fixture with the shipped default shelf lives, and its answers worked out BY HAND:
#   x + y with y = 10:  28, 17, 17, 16, 15, 14, 14, 12
#   z = 5 and z = 9:    every sum exceeds z -> 8 of 8 exposed
#   z = 14:             28, 17, 17, 16, 15 exceed 14 -> 5 of 8
#   exposed now (x > z): z=5 -> 18, 7, 7, 6 (4);  z=9 -> 18 (1);  z=14 -> 18 (1)
FIXTURE_SHELF_LIVES = (18, 7, 7, 6, 5, 4, 4, 2)
FIXTURE_Y, FIXTURE_Z = 10, (5, 9, 14)
FIXTURE_ANSWER = [(8, 4), (8, 1), (5, 1)]          # (exposed, exposed now) per z


def _dt(x: int) -> DataType:
    return DataType("t", x, "fixture", "regulation", ())


def _sc(z: int, as_of: int) -> Scenario:
    return Scenario("fixture", (as_of + z, as_of + z), as_of + z, "fixture", ())


def _tally(rows: list[Assessment]) -> tuple[int, int]:
    return sum(a.status != SAFE for a in rows), sum(a.status == EXPOSED_NOW for a in rows)


def _checks(cfg: Config, results: dict[str, list[Assessment]]) -> list[Check]:
    as_of = cfg.as_of_year
    # Boundary behaviour of the inequality itself (x=5, y=4, z=9): equality is safe, one
    # more year of shelf life tips it over.
    at, over = assess(_dt(5), _sc(9, as_of), 4, as_of), assess(_dt(6), _sc(9, as_of), 4, as_of)
    now = assess(_dt(10), _sc(9, as_of), 0, as_of)
    fixture = [_tally([assess(_dt(x), _sc(z, as_of), FIXTURE_Y, as_of)
                       for x in FIXTURE_SHELF_LIVES]) for z in FIXTURE_Z]
    counts = [sum(a.status != SAFE for a in results[s.name]) for s in cfg.scenarios]
    checks = [
        Check("Mosca: x + y = z is safe, x + y = z + 1 is exposed (strict >)",
              at.status == SAFE and over.status == EXPOSED and over.exposure_years == 1),
        Check("Mosca: x > z is exposed even with zero migration time",
              now.status == EXPOSED_NOW and now.safe_by_year == as_of - 1),
        Check("Mosca: engine reproduces the hand-computed fixture table",
              fixture == FIXTURE_ANSWER, f"(exposed, exposed now) = {fixture}"),
        Check("Mosca: exposure never falls as the CRQC arrives earlier",
              counts == sorted(counts, reverse=True), f"{counts}"),
    ]
    name = "Mosca: the loaded config reproduces the hand-computed table"
    if not matches_shipped_defaults(cfg):
        checks.append(not_run(name, "config differs from the shipped defaults the table uses"))
    else:
        got = [_tally(results[s.name]) for s in cfg.scenarios]
        checks.append(Check(name, got == FIXTURE_ANSWER, f"{got}"))
    return checks
