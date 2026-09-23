"""Migration priority as a stated rule: an ordered list in pqc_config.yaml, first match wins.

A priority is a PLANNING AID, not a risk rating from a certified assessment. It is
computed from three facts about a register row, each of which a reader can check:

    category     the scanner's class: quantum-vulnerable | classically weak | PQC-ready
    classified   whether the asset register maps the file to any data type
    base_status  the Mosca status of the row's governing data type in the Base scenario:
                 EXPOSED NOW | EXPOSED | safe   (none when unclassified)

Those facts take only 3 x 3 = 9 classified combinations plus 3 unclassified ones, so the
rule list is checked exhaustively on load. Every combination must reach a rule (no row
is ever left without a priority), and every rule must be the first match for at least
one combination (a rule that can never fire is a configuration mistake, not a no-op).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_scanner import CW, PQ, QV

# The Mosca statuses (mosca.py imports these, so both modules share one spelling).
EXPOSED_NOW = "EXPOSED NOW"          # x > z: exposed even if migration finished today
EXPOSED = "EXPOSED"                  # x + y > z: exposed at the planned migration speed
SAFE = "safe"
STATUSES = (EXPOSED_NOW, EXPOSED, SAFE)
CATEGORIES = (QV, CW, PQ)
CONDITIONS = ("category", "classified", "base_status")


class RuleError(ValueError):
    """The priority rule list is malformed, incomplete or has an unreachable rule."""


@dataclass(frozen=True)
class PriorityRule:
    rule_id: str
    priority: str
    reason: str
    category: tuple[str, ...] | None      # None = any
    classified: bool | None
    base_status: tuple[str, ...] | None

    def matches(self, category: str, classified: bool, base_status: str | None) -> bool:
        if self.category is not None and category not in self.category:
            return False
        if self.classified is not None and classified != self.classified:
            return False
        if self.base_status is not None and base_status not in self.base_status:
            return False
        return True

    def describe(self) -> str:
        parts = []
        if self.category is not None:
            parts.append("class is " + " or ".join(self.category))
        if self.classified is not None:
            parts.append("in the asset register" if self.classified else
                         "NOT in the asset register")
        if self.base_status is not None:
            parts.append("Base-window status is " + " or ".join(self.base_status))
        return " and ".join(parts) or "any finding"


def combinations() -> list[tuple[str, bool, str | None]]:
    """Every (category, classified, base_status) a register row can have."""
    return ([(c, True, s) for c in CATEGORIES for s in STATUSES]
            + [(c, False, None) for c in CATEGORIES])


def first_match(rules: tuple[PriorityRule, ...], category: str, classified: bool,
                base_status: str | None) -> PriorityRule:
    for rule in rules:
        if rule.matches(category, classified, base_status):
            return rule
    raise RuleError(f"no priority rule matches category={category!r}, "
                    f"classified={classified}, base_status={base_status!r}")


def _list(value: Any, allowed: tuple[str, ...], where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(v not in allowed for v in value):
        raise RuleError(f"{where}: expected a non-empty list drawn from {list(allowed)}, "
                        f"got {value!r}")
    return tuple(value)


def _rule(raw: Any, i: int, levels: tuple[str, ...]) -> PriorityRule:
    where = f"priority_rules[{i}]"
    if not isinstance(raw, dict):
        raise RuleError(f"{where}: expected a mapping")
    missing = [k for k in ("id", "priority", "when", "reason") if k not in raw]
    if missing:
        raise RuleError(f"{where}: missing field(s) {missing}")
    if raw["priority"] not in levels:
        raise RuleError(f"{where}.priority: {raw['priority']!r} is not in priority_levels")
    when = raw["when"] or {}
    if not isinstance(when, dict) or any(k not in CONDITIONS for k in when):
        raise RuleError(f"{where}.when: conditions must be drawn from {list(CONDITIONS)}")
    classified = when.get("classified")
    if classified is not None and not isinstance(classified, bool):
        raise RuleError(f"{where}.when.classified: expected true or false")
    return PriorityRule(
        str(raw["id"]), raw["priority"], str(raw["reason"]).strip(),
        _list(when["category"], CATEGORIES, f"{where}.when.category") if "category" in when
        else None,
        classified,
        _list(when["base_status"], STATUSES, f"{where}.when.base_status")
        if "base_status" in when else None)


def parse_rules(raw_rules: Any, raw_levels: Any) -> tuple[tuple[PriorityRule, ...],
                                                         tuple[str, ...]]:
    if (not isinstance(raw_levels, list) or not raw_levels
            or not all(isinstance(v, str) for v in raw_levels)
            or len(set(raw_levels)) != len(raw_levels)):
        raise RuleError("priority_levels: expected a list of distinct names")
    levels = tuple(raw_levels)
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RuleError("priority_rules: expected a non-empty list")
    rules = tuple(_rule(r, i, levels) for i, r in enumerate(raw_rules))
    ids = [r.rule_id for r in rules]
    if len(set(ids)) != len(ids):
        raise RuleError("priority_rules: rule ids must be unique")
    fired = {first_match(rules, *combo).rule_id for combo in combinations()}  # gap -> raises
    dead = [r.rule_id for r in rules if r.rule_id not in fired]
    if dead:
        raise RuleError(f"priority_rules: rule(s) {dead} can never fire (an earlier rule "
                        "always matches first)")
    unused = [lvl for lvl in levels if lvl not in {r.priority for r in rules}]
    if unused:
        raise RuleError(f"priority_levels: {unused} are never assigned by any rule")
    return rules, levels
