"""Print the results and write them out: fraud_report.md, fraud_summary.csv, fraud_run_log.json.

Every number and every verdict in the report is computed from this run. The prose around
them is fixed, but the sentence that says who won is chosen by the numbers.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np

from analysis import Comparison, Operating, ShotRow
from config import (FPR_TARGET, N_QUBITS, SUBSAMPLE_PER_CLASS,
                    VERSION, Check)


@dataclass(frozen=True)
class Results:
    run_time: datetime
    miss_cost: float
    review_cost: float
    ops: Sequence[Operating]                     # full models first, then fair ones
    baselines: dict[str, float]
    sensitivity: list[tuple[int, str, float]]
    comparisons: Sequence[Comparison]
    edge_notes: Sequence[str]
    draw_overlap: float
    train_frauds: int
    without_time: tuple[str, float, float]      # (model, PR-AUC with Time, without Time)
    stability: dict[str, list[float]]
    shots: Sequence[ShotRow]
    kernel_spread: float
    kernel_mean: float
    train_size: int
    circuits: dict[str, int]
    test_size: int
    test_frauds: int
    prevalence: float
    mean_fraud_amount: float


def summary_rows(r: Results) -> list[dict[str, str]]:
    rows = []
    for o in r.ops:
        t = o.test
        rows.append({
            "Model": o.model.name,
            "Training data": (f"full: {r.train_size:,} rows, 30 features"
                              if o.model.regime == "full"
                              else f"subsample: {2 * SUBSAMPLE_PER_CLASS} rows, {N_QUBITS} "
                              "features"),
            "PR-AUC": f"{o.pr_auc:.3f}",
            f"Recall at {FPR_TARGET:.1%} FPR": f"{o.recall_at_fpr:.1%}",
            "Alerts": f"{t.tp + t.fp:,}",
            "Frauds caught": f"{t.tp} of {t.tp + t.fn}",
            "False alarms": f"{t.fp:,}",
            "Test cost": f"{o.cost:,.0f}",
            "Accuracy (not used to rank)": f"{t.accuracy:.3%}",
        })
    return rows


def markdown_table(rows: list[dict[str, str]]) -> str:
    head = list(rows[0])
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(row[h] for h in head) + " |" for row in rows]
    return "\n".join(lines)


def print_summary(r: Results) -> None:
    for row in summary_rows(r):
        print(f"  {row['Model']:<20} {row['Training data']:<34} PR-AUC {row['PR-AUC']}  "
              f"recall@FPR {row[f'Recall at {FPR_TARGET:.1%} FPR']:>6}  "
              f"caught {row['Frauds caught']:>9}  false alarms {row['False alarms']:>6}  "
              f"cost {row['Test cost']:>7}")
    for name, cost in r.baselines.items():
        print(f"  {name:<55} cost {cost:>7,.0f}")


def verdict(r: Results) -> list[str]:
    """Plain statements of who won, chosen by the numbers."""
    by_auc = max(r.ops, key=lambda o: o.pr_auc)
    by_cost = min(r.ops, key=lambda o: o.cost)
    fair = [o for o in r.ops if o.model.regime == "subsample"]
    shown = {o.model.name: round(o.pr_auc, 3) for o in fair}      # as printed: ties are ties
    top = max(shown.values())
    leaders = [name for name, value in shown.items() if value == top]
    quantum = next(o for o in fair if o.model.name == "Quantum kernel SVM")
    rank = 1 + sum(value > shown[quantum.model.name] for value in shown.values())
    q_vs = r.comparisons[0]
    means = {name: sum(v) / len(v) for name, v in r.stability.items()}
    ordered = sorted(means, key=lambda n: -means[n])
    q_values = r.stability["Quantum kernel SVM"]
    spread = {name: max(v) - min(v) for name, v in r.stability.items()}
    draws_line = (f"Across {len(q_values)} subsample draws (overlapping: any two share about "
                  f"{r.draw_overlap:.0%} of their frauds), {ordered[0]} has the best "
                  f"mean PR-AUC ({means[ordered[0]]:.3f}); the quantum kernel SVM ranks "
                  f"{ordered.index('Quantum kernel SVM') + 1} of {len(ordered)} "
                  f"({means['Quantum kernel SVM']:.3f}, range {min(q_values):.3f} to "
                  f"{max(q_values):.3f}). The widest swing from draw to draw belongs to "
                  f"{max(spread, key=spread.get)}.")
    half_width = (q_vs.high - q_vs.low) / 2
    gap = top - min(shown.values())
    within_noise = (f"; all {len(fair)} sit within {gap:.3f} of each other, inside the "
                    f"+/-{half_width:.3f} bootstrap noise shown below" if gap < half_width else "")
    fair_line = (f"Fair comparison, primary subsample: {' and '.join(leaders)} "
                 f"{'tie for first' if len(leaders) > 1 else 'comes first'} at PR-AUC {top:.3f}"
                 f"{within_noise}. "
                 f"The quantum kernel SVM ranks {rank} of {len(fair)}, at "
                 f"{shown[quantum.model.name]:.3f}.")
    if q_vs.significant and q_vs.difference < 0:
        quantum_line = (f"The quantum kernel lost: its PR-AUC is {-q_vs.difference:.3f} lower than "
                        f"{q_vs.label.split(' vs ')[1]}, and the 95% bootstrap interval "
                        f"({q_vs.low:+.3f} to {q_vs.high:+.3f}) excludes zero.")
    elif q_vs.significant:
        quantum_line = (f"The quantum kernel won the fair comparison by {q_vs.difference:.3f} "
                        f"PR-AUC (95% interval {q_vs.low:+.3f} to {q_vs.high:+.3f}).")
    else:
        quantum_line = (f"The quantum kernel is not detectably different from "
                        f"{q_vs.label.split(' vs ')[1]}: the PR-AUC difference "
                        f"{q_vs.difference:+.3f} has a 95% interval of {q_vs.low:+.3f} to "
                        f"{q_vs.high:+.3f}, which includes zero.")
    return [
        f"Best model overall: {by_auc.label}, PR-AUC {by_auc.pr_auc:.3f}, catching "
        f"{by_auc.recall_at_fpr:.0%} of fraud at {FPR_TARGET:.1%} false-positive rate.",
        f"Lowest total cost: {by_cost.label}, {by_cost.cost:,.0f} on the test set "
        f"(review nothing: {r.baselines['Review nothing']:,.0f}).",
        fair_line,
        quantum_line,
        draws_line,
    ]


def write_csv(r: Results, path: Path) -> None:
    rows = summary_rows(r)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_run_log(r: Results, checks: Sequence[Check], outputs: Sequence[Path], data: dict,
                  path: Path) -> None:
    log = {
        "run_timestamp": r.run_time.isoformat(timespec="seconds"),
        "automation": "fraud-detection-classical-vs-quantum",
        "version": VERSION,
        "source": data,
        "processing": {
            "costs": {"miss": r.miss_cost, "review": r.review_cost},
            "models": [{"model": o.model.name, "regime": o.model.regime,
                        "params": {k: (float(v) if isinstance(v, (float, np.floating)) else v)
                                   for k, v in o.model.params.items()},
                        "configs_tried": o.model.configs_tried,
                        "threshold_from_validation": (None if np.isinf(o.threshold)
                                                      else float(o.threshold)),
                        "test_pr_auc": round(o.pr_auc, 4), "test_cost": o.cost}
                       for o in r.ops],
            "subsample_stability_pr_auc": {k: [round(v, 4) for v in vals]
                                           for k, vals in r.stability.items()},
        },
        "output": {"files": [p.name for p in outputs]},
        "checks": {"passed": sum(c.status == "PASS" for c in checks),
                   "failed": [c.name for c in checks if c.status == "FAIL"],
                   "not_run": [c.name for c in checks if c.status == "NOT RUN"]},
        "exceptions": [],
    }
    path.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
