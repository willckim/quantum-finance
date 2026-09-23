"""Load the asset register and classify a scanned file by it (and only by it).

The register is human-supplied: an organization states which data each system, folder or
file handles. This module never infers classification from file names, contents or
findings. A file no entry covers is UNCLASSIFIED, and the migration register says so.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from common import HERE, Check
from config import Config, DataType

DEFAULT_REGISTER = HERE / "asset_register.yaml"
UNCLASSIFIED = "Unclassified: needs owner review"
# Line-ending-normalized SHA-256 of the SHIPPED asset_register.yaml. Checks worked out by
# hand for the sample run only when the loaded register still has exactly this content;
# an organization's own register (in place or elsewhere) gets NOT RUN, never a false FAIL.
SHIPPED_REGISTER_FINGERPRINT = "499197697a11c988f6eee7a05ae816a05695f658ff3890ec25ffcaab4e38d090"


class RegisterError(ValueError):
    """The asset register is missing a field or refers to an unknown data type."""


@dataclass(frozen=True)
class Asset:
    asset_id: str
    name: str
    owner: str
    paths: tuple[str, ...]
    data_types: tuple[DataType, ...]


@dataclass(frozen=True)
class AssetRegister:
    assets: tuple[Asset, ...]
    path_label: str                 # file name only: never a machine path
    sha256: str = ""                # of the exact bytes parsed (evidence)
    fingerprint: str = ""           # line-ending-normalized, to recognize the shipped file


@dataclass(frozen=True)
class Classification:
    assets: tuple[Asset, ...]
    data_types: tuple[DataType, ...]         # union, longest shelf life first
    governing: DataType | None               # the one the exposure is computed from

    @property
    def classified(self) -> bool:
        return self.governing is not None

    @property
    def note(self) -> str:
        if not self.classified:
            return UNCLASSIFIED
        if len(self.data_types) == 1:
            return f"{self.governing.name} ({self.governing.shelf_life} yr)"
        listed = "; ".join(f"{d.name} {d.shelf_life} yr" for d in self.data_types)
        return (f"{self.governing.name}: longest shelf life of {len(self.data_types)} "
                f"mapped data types ({listed})")


def _text(raw: dict, key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegisterError(f"{where}.{key}: expected non-empty text")
    return value.strip()


def _names(raw: dict, key: str, where: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not value or not all(
            isinstance(v, str) and v.strip() for v in value):
        raise RegisterError(f"{where}.{key}: expected a non-empty list of text")
    return [v.strip() for v in value]


def fingerprint(data: bytes) -> str:
    """SHA-256 with CRLF folded to LF, so a Windows checkout reads as the same content."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """Only "*" is a wildcard (matching any characters, "/" included). Everything else,
    including "[", "]" and "?", is literal: a path like app/[id]/page.tsx must match
    itself and nothing else."""
    return re.compile(".*".join(re.escape(part) for part in pattern.split("*")))


def matches(rel_path: str, pattern: str) -> bool:
    return pattern_regex(pattern).fullmatch(rel_path) is not None


def parse_register(raw: Any, cfg: Config, path_label: str = "asset_register.yaml",
                   data: bytes = b"") -> AssetRegister:
    if not isinstance(raw, dict) or not isinstance(raw.get("assets"), list) or not raw["assets"]:
        raise RegisterError("asset register: expected a non-empty 'assets' list")
    known = {d.name: d for d in cfg.data_types}
    assets = []
    for i, entry in enumerate(raw["assets"]):
        where = f"assets[{i}]"
        if not isinstance(entry, dict):
            raise RegisterError(f"{where}: expected a mapping")
        paths = _names(entry, "paths", where)
        if any(p.startswith("/") or "\\" in p or ".." in p.split("/") for p in paths):
            raise RegisterError(f"{where}.paths: use forward-slash paths relative to the "
                                "scanned folder, without '..'")
        names = _names(entry, "data_types", where)
        unknown = [n for n in names if n not in known]
        if unknown:
            raise RegisterError(f"{where}.data_types: {unknown} are not data types in the "
                                "config")
        assets.append(Asset(_text(entry, "id", where), _text(entry, "name", where),
                            _text(entry, "owner", where), tuple(paths),
                            tuple(known[n] for n in names)))
    ids = [a.asset_id for a in assets]
    if len(set(ids)) != len(ids):
        raise RegisterError("asset register: asset ids must be unique")
    return AssetRegister(tuple(assets), path_label, hashlib.sha256(data).hexdigest() if data
                         else "", fingerprint(data) if data else "")


def is_shipped(register: AssetRegister) -> bool:
    return register.fingerprint == SHIPPED_REGISTER_FINGERPRINT


def register_path() -> Path:
    override = os.environ.get("PQC_ASSET_REGISTER", "").strip()
    return Path(override).resolve() if override else DEFAULT_REGISTER


def load_register(cfg: Config, path: Path | None = None) -> AssetRegister:
    path = path or register_path()
    try:
        data = path.read_bytes()             # hash and parse the SAME bytes
        raw = yaml.safe_load(data.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RegisterError(f"could not read {path.name}: {exc}") from exc
    return parse_register(raw, cfg, path.name, data)


def classify(register: AssetRegister, rel_path: str) -> Classification:
    """Every asset whose pattern matches the path; data types unioned, longest first."""
    matched = tuple(a for a in register.assets if any(matches(rel_path, p) for p in a.paths))
    union = {d.name: d for a in matched for d in a.data_types}
    ordered = tuple(sorted(union.values(), key=lambda d: (-d.shelf_life, d.name)))
    return Classification(matched, ordered, ordered[0] if ordered else None)


def unused_patterns(register: AssetRegister, files: list[str]) -> list[str]:
    """Patterns that matched no scanned file: a sign the register has gone stale."""
    return [f"{a.asset_id}: {p}" for a in register.assets for p in a.paths
            if not any(matches(f, p) for f in files)]


# ======================================================================================
# SELF-CHECKS
# ======================================================================================
_BROKEN = (
    ("unknown data type", (0, "data_types"), ["Customer card data"], "not data types"),
    ("duplicate asset id", (1, "id"), "CARD-SETTLE", "unique"),
    ("empty paths", (0, "paths"), [], "paths"),
    ("absolute path", (0, "paths"), ["/etc/passwd"], "relative"),
    ("parent-directory path", (0, "paths"), ["../outside.py"], "relative"),
    ("missing owner", (0, "owner"), "", "owner"),
)


def register_checks(cfg: Config) -> list[Check]:
    raw = yaml.safe_load(DEFAULT_REGISTER.read_text(encoding="utf-8-sig"))
    try:
        reg = parse_register(raw, cfg)
    except RegisterError as exc:
        return [Check("Register: shipped asset_register.yaml loads", False, str(exc))]
    accepted = []
    for label, (index, key), value, must_mention in _BROKEN:
        broken = copy.deepcopy(raw)
        broken["assets"][index][key] = value
        try:
            parse_register(broken, cfg)
            accepted.append(label)
        except RegisterError as exc:
            if must_mention not in str(exc):
                accepted.append(f"{label} (wrong message: {exc})")
    literal = [("app/[id]/page.tsx", "app/[id]/page.tsx", True),
               ("app/i/page.tsx", "app/[id]/page.tsx", False),
               ("ax.py", "a?.py", False), ("a?.py", "a?.py", True),
               ("batch_jobs/x/y.py", "batch_jobs/*", True), ("batch_jobsX.py", "batch_jobs/*", False)]
    wrong = [(path, pat) for path, pat, want in literal if matches(path, pat) != want]
    return [Check("Register: shipped asset_register.yaml loads", True,
                  f"{len(reg.assets)} assets"),
            Check("Register: only '*' is a wildcard; '[', ']' and '?' match literally",
                  not wrong, f"wrong: {wrong}" if wrong else f"{len(literal)} cases"),
            Check("Register: each broken variant is rejected, naming the field",
                  not accepted, f"{len(_BROKEN) - len(accepted)}/{len(_BROKEN)} rejected"
                  + (f"; {accepted}" if accepted else ""))]
