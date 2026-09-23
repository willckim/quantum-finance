"""Load and validate pqc_config.yaml: the assessment inputs an organization replaces.

The Mosca analysis is only as good as its inputs, and a typo in a config file should
stop the run with a clear message rather than produce a confident report. So every field
is checked on load: types, ranges, that each cited source exists in sources.py, that an
assumption is labeled as one, and that the scenario windows are ordered, do not overlap
and contain their planning year.

yaml.safe_load is used, never yaml.load: a config file is data and must not be able to
construct arbitrary Python objects.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from common import HERE, Check
from sources import SOURCES

DEFAULT_CONFIG = HERE / "pqc_config.yaml"
BASIS_TYPES = ("regulation", "assumption", "policy")
# The rest of the toolkit reads scenarios by these names (the vendor rating and the report
# use Base and Pessimistic), so the names are fixed; only their windows are configurable.
SCENARIO_NAMES = ("Pessimistic", "Base", "Optimistic")
MAX_YEAR = 2100


class ConfigError(ValueError):
    """The config file is missing a field or holds an inconsistent value."""


@dataclass(frozen=True)
class DataType:
    name: str
    shelf_life: int                 # years the data must stay confidential
    basis: str                      # the rule or assumption, in plain words
    basis_type: str                 # regulation | assumption | policy
    source_keys: tuple[str, ...]

    @property
    def assumption(self) -> bool:
        return self.basis_type == "assumption"


@dataclass(frozen=True)
class Scenario:
    name: str
    window: tuple[int, int]         # earliest and latest CRQC year in this scenario
    crqc_year: int                  # the planning year the calculation uses
    basis: str
    source_keys: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.name} ({self.window[0]}-{self.window[1]})"


@dataclass(frozen=True)
class Config:
    as_of_year: int
    migration_years: int
    migration_sources: tuple[str, ...]
    data_types: tuple[DataType, ...]
    scenarios: tuple[Scenario, ...]
    path_label: str                 # file name only: never a machine path


# ---- field readers (each raises ConfigError naming the field) ------------------------
def _get(raw: dict, key: str, where: str) -> Any:
    if not isinstance(raw, dict) or key not in raw:
        raise ConfigError(f"{where}: missing field '{key}'")
    return raw[key]


def _int(raw: dict, key: str, where: str, low: int, high: int) -> int:
    value = _get(raw, key, where)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{where}.{key}: expected a whole number {low}-{high}, got {value!r}")
    return value


def _text(raw: dict, key: str, where: str) -> str:
    value = _get(raw, key, where)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key}: expected non-empty text")
    return value.strip()


def _sources(raw: dict, where: str, required: bool = True) -> tuple[str, ...]:
    keys = _get(raw, "sources", where)
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise ConfigError(f"{where}.sources: expected a list of source keys")
    unknown = [k for k in keys if k not in SOURCES]
    if unknown:
        raise ConfigError(f"{where}.sources: unknown source key(s) {unknown}; "
                          "add them to sources.py or use basis_type: policy")
    if required and not keys:
        raise ConfigError(f"{where}.sources: at least one source is required")
    return tuple(keys)


# ---- sections ------------------------------------------------------------------------
def _data_type(raw: dict, i: int) -> DataType:
    where = f"data_types[{i}]"
    name = _text(raw, "name", where)
    if ";" in name:
        raise ConfigError(f"{where}.name: ';' is reserved (it separates data types in the "
                          "vendor tracker)")
    basis_type = _text(raw, "basis_type", where)
    if basis_type not in BASIS_TYPES:
        raise ConfigError(f"{where}.basis_type: expected one of {BASIS_TYPES}, got "
                          f"{basis_type!r}")
    basis = _text(raw, "basis", where)
    if basis_type == "assumption" and "assumption" not in basis.lower():
        raise ConfigError(f"{where}.basis: an assumption must say so in its basis text")
    return DataType(name, _int(raw, "shelf_life_years", where, 0, 100), basis, basis_type,
                    _sources(raw, where, required=basis_type != "policy"))


def _scenario(raw: dict, i: int, as_of: int) -> Scenario:
    where = f"scenarios[{i}]"
    window = _get(raw, "window", where)
    if (not isinstance(window, list) or len(window) != 2
            or not all(isinstance(y, int) and not isinstance(y, bool) for y in window)
            or not as_of < window[0] <= window[1] <= MAX_YEAR):
        raise ConfigError(f"{where}.window: expected [earliest, latest] years after "
                          f"{as_of} and no later than {MAX_YEAR}, got {window!r}")
    planning = _int(raw, "planning_year", where, window[0], window[1])
    return Scenario(_text(raw, "name", where), (window[0], window[1]), planning,
                    _text(raw, "basis", where), _sources(raw, where))


def parse_config(raw: Any, path_label: str = "pqc_config.yaml") -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config: expected a mapping at the top level")
    as_of = _int(raw, "as_of_year", "config", 2000, 2100)
    migration = _get(raw, "migration", "config")
    types = _get(raw, "data_types", "config")
    scens = _get(raw, "scenarios", "config")
    if not isinstance(types, list) or not types:
        raise ConfigError("config.data_types: expected a non-empty list")
    if not isinstance(scens, list) or len(scens) != 3:
        raise ConfigError("config.scenarios: expected exactly three scenarios "
                          "(pessimistic, base, optimistic)")
    data_types = tuple(_data_type(t, i) for i, t in enumerate(types))
    names = [d.name.casefold() for d in data_types]
    if len(set(names)) != len(names):
        raise ConfigError("config.data_types: data type names must be unique (ignoring case)")
    scenarios = tuple(_scenario(s, i, as_of) for i, s in enumerate(scens))
    if tuple(s.name for s in scenarios) != SCENARIO_NAMES:
        raise ConfigError(f"config.scenarios: names must be {list(SCENARIO_NAMES)} in that "
                          f"order, got {[s.name for s in scenarios]}")
    for a, b in zip(scenarios, scenarios[1:]):
        if a.window[1] >= b.window[0]:
            raise ConfigError(f"config.scenarios: windows must be ordered and not overlap "
                              f"({a.name} {a.window} vs {b.name} {b.window})")
    # Sorted longest shelf life first, so tables and the chart lead with the riskiest data.
    ordered = tuple(sorted(data_types, key=lambda d: (-d.shelf_life, d.name)))
    return Config(as_of, _int(migration, "years", "migration", 0, 50),
                  _sources(migration, "migration"), ordered, scenarios, path_label)


def matches_shipped_defaults(cfg: Config) -> bool:
    """True when cfg holds exactly the shipped default inputs (whatever the file is called).

    Checks whose hand-worked answers assume the defaults run only when this is True, and
    report NOT RUN otherwise, so an organization's own inputs never produce a false FAIL.
    """
    shipped = parse_config(yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8-sig")))
    fields = ("as_of_year", "migration_years", "migration_sources", "data_types", "scenarios")
    return all(getattr(cfg, f) == getattr(shipped, f) for f in fields)


def config_path() -> Path:
    override = os.environ.get("PQC_CONFIG", "").strip()
    return Path(override).resolve() if override else DEFAULT_CONFIG


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))   # tolerate a BOM
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not read {path.name}: {exc}") from exc
    return parse_config(raw, path.name)


# ======================================================================================
# SELF-CHECKS
# ======================================================================================
# Each broken variant changes ONE field of the shipped file and must be rejected with a
# message naming that field. Together with the clean load, that tests both cases.
_BROKEN = (
    ("negative shelf life", ("data_types", 0, "shelf_life_years"), -1, "shelf_life_years"),
    ("unknown source key", ("data_types", 0, "sources"), ["NOT-A-SOURCE"], "unknown source"),
    ("unlabeled assumption", ("data_types", -1, "basis"), "two years", "assumption"),
    ("bad basis_type", ("data_types", 0, "basis_type"), "guess", "basis_type"),
    ("planning year outside window", ("scenarios", 1, "planning_year"), 2050,
     "planning_year"),
    ("overlapping windows", ("scenarios", 1, "window"), [2031, 2037], "overlap"),
    ("window before as-of year", ("scenarios", 0, "window"), [2020, 2032], "window"),
    ("migration years not a number", ("migration", "years"), "ten", "years"),
    ("true/false used as a number", ("data_types", 0, "shelf_life_years"), True,
     "shelf_life_years"),
    ("regulation with no sources", ("data_types", 0, "sources"), [], "at least one source"),
    ("duplicate data type name", ("data_types", 1, "name"), "m&a DOCUMENTS", "unique"),
    ("';' in a data type name", ("data_types", 0, "name"), "M&A; deals", "reserved"),
    ("renamed scenario", ("scenarios", 1, "name"), "Central", "names must be"),
    ("window past the year cap", ("scenarios", 2, "window"), [2038, 99999], "window"),
)
# Variants that must be ACCEPTED: a guard tested only against bad input proves it can
# fire, not that it fires for the right reason.
_VALID = (
    ("policy basis with no sources", ("data_types", 0), {
        "name": "M&A documents", "shelf_life_years": 10, "basis": "Retention schedule RS-12",
        "basis_type": "policy", "sources": []}),
    ("planning year at the window edge", ("scenarios", 1, "planning_year"), 2037),
)


def _mutate(raw: dict, path: tuple, value: Any) -> dict:
    broken = copy.deepcopy(raw)
    node = broken
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return broken


def config_checks() -> list[Check]:
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8-sig"))
    try:
        cfg = parse_config(raw)
        loaded = Check("Config: shipped pqc_config.yaml loads and validates", True,
                       f"{len(cfg.data_types)} data types, {len(cfg.scenarios)} scenarios")
    except ConfigError as exc:
        return [Check("Config: shipped pqc_config.yaml loads and validates", False, str(exc))]
    missed = []
    for label, path, value, must_mention in _BROKEN:
        try:
            parse_config(_mutate(raw, path, value))
            missed.append(f"{label} (accepted)")
        except ConfigError as exc:
            if must_mention not in str(exc):
                missed.append(f"{label} (wrong message: {exc})")
    refused = []
    for label, path, value in _VALID:
        try:
            parse_config(_mutate(raw, path, value))
        except ConfigError as exc:
            refused.append(f"{label} ({exc})")
    return [loaded,
            Check("Config: each broken variant is rejected, naming the field",
                  not missed, f"{len(_BROKEN) - len(missed)}/{len(_BROKEN)} rejected"
                  + (f"; {missed}" if missed else "")),
            Check("Config: valid variants (policy basis, window-edge year) are accepted",
                  not refused, f"{len(_VALID) - len(refused)}/{len(_VALID)} accepted"
                  + (f"; {refused}" if refused else ""))]
