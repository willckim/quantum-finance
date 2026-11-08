"""Mutation tests: proof that the self-checks in run_all.py can fail.

Each mutant is a plausible wrong implementation. The check that guards it must turn FAIL,
and the real code must PASS the same check on the same inputs, so the check is shown to
discriminate rather than to reject everything. Runs on small synthetic inputs in seconds,
with no dataset needed:  py fraud_mutation_tests.py
"""

from __future__ import annotations

import contextlib
import sys
from typing import Callable

import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.svm import SVC

import analysis
import data
import metrics
import quantum_kernel
from config import SEED, Check
from models import Scored


@contextlib.contextmanager
def patched(module, name: str, replacement):
    original = getattr(module, name)
    setattr(module, name, replacement)
    try:
        yield
    finally:
        setattr(module, name, original)


def synthetic_labels() -> np.ndarray:
    """50,000 rows, 1% fraud: enough for a 200-fraud subsample from a 60% training split."""
    y = np.zeros(50_000, dtype=np.int8)
    y[np.random.default_rng([SEED, 3]).choice(50_000, 500, replace=False)] = 1
    return y


def synthetic_quantum_model() -> Scored:
    rng = np.random.default_rng([SEED, 5])
    F = rng.normal(size=(60, 4))
    y = (F[:, 0] + 0.5 * rng.normal(size=60) > 0).astype(int)
    s_sub, s_test = quantum_kernel.feature_states(0.3 * F[:40], 1), \
        quantum_kernel.feature_states(0.3 * F[40:], 1)
    model = SVC(kernel="precomputed", C=1.0).fit(quantum_kernel.kernel(s_sub, s_sub), y[:40])
    extra = {"model": model, "states_sub": s_sub, "states_test": s_test}
    return Scored("Quantum kernel SVM", "subsample", {}, np.zeros(1), np.zeros(1), 1, extra)


def failed(checks: list[Check], keyword: str) -> bool:
    return any(c.status == "FAIL" for c in checks if keyword in c.name)


Y = synthetic_labels()
SPLITS = data.split(Y)


def unstratified_split(y: np.ndarray) -> data.Splits:
    rows = np.arange(len(y))
    train, rest = train_test_split(rows, test_size=0.4, random_state=SEED)
    val, test = train_test_split(rest, test_size=0.5, random_state=SEED)
    return data.Splits(np.sort(train), np.sort(val), np.sort(test))


def leaky_subsample(y: np.ndarray, train: np.ndarray, draw: int) -> np.ndarray:
    rng = np.random.default_rng([SEED, 31, draw])
    rows = np.arange(len(y))                                  # ignores the split: leakage
    return np.sort(np.concatenate([rng.choice(rows[y == 1], 200, replace=False),
                                   rng.choice(rows[y == 0], 200, replace=False)]))


REAL_FEATURE_STATES = quantum_kernel.feature_states


def states_missing_factor_two(F, reps, entangle=True):
    return REAL_FEATURE_STATES(F / 2, reps, entangle)


def scores_without_intercept(model, states_eval, states_train, kernel_fn=quantum_kernel.kernel,
                             chunk_rows=None):
    support = states_train[model.support_]
    return kernel_fn(states_eval, support) @ model.dual_coef_[0]


def cost_without_true_alert_reviews(self, miss_cost, review_cost):
    return miss_cost * self.fn + review_cost * self.fp


def recall_dropping_tied_points(y, scores, fpr_target=metrics.FPR_TARGET):
    fpr, tpr, _ = metrics.roc_curve(y, scores)            # the original, tie-blind version
    return float(tpr[fpr <= fpr_target + 1e-12].max())


def reducer_fitted_on_everything(X, rows):
    return REAL_FIT_REDUCER(X, np.arange(len(X)))          # peeks at every row


REAL_FIT_REDUCER = data.fit_reducer


def recall_strict(y, scores, fpr_target=metrics.FPR_TARGET):
    fpr, tpr, _ = metrics.roc_curve(y, scores)
    return float(tpr[fpr < fpr_target].max())


# (mutant name, the check that must catch it, run the check, apply the mutant)
CASES: list[tuple[str, str, Callable[[], list[Check]], Callable]] = [
    ("split ignores the fraud rate (not stratified)", "fraud rate",
     lambda: data.split_checks(data.split(Y), Y),
     lambda: patched(data, "split", unstratified_split)),
    ("subsample drawn from all rows, leaking validation and test", "training rows",
     lambda: data.subsample_checks(Y, SPLITS),
     lambda: patched(data, "draw_subsample", leaky_subsample)),
    ("feature map with every angle halved", "equal qiskit",
     lambda: quantum_kernel.feature_map_checks(4),
     lambda: patched(quantum_kernel, "feature_states", states_missing_factor_two)),
    ("quantum-SVM scorer drops the intercept", "scorer",
     lambda: [analysis.scorer_check(synthetic_quantum_model())],
     lambda: patched(analysis, "svm_scores", scores_without_intercept)),
    ("cost forgets that true alerts are reviewed too", "cost at threshold 8",
     metrics.metric_answer_key_checks,
     lambda: patched(metrics.Confusion, "cost", cost_without_true_alert_reviews)),
    ("recall at FPR uses < instead of <=", "recall at FPR <= 1/7",
     metrics.metric_answer_key_checks,
     lambda: patched(metrics, "recall_at_fpr", recall_strict)),
    ("recall at FPR drops tied operating points", "tied in pairs",
     metrics.metric_answer_key_checks,
     lambda: patched(metrics, "recall_at_fpr", recall_dropping_tied_points)),
    ("feature reduction fitted on every row, not just the subsample", "ignores every row",
     lambda: [data.reducer_leakage_check(np.random.default_rng(1).normal(size=(3_000, 30)),
                                         np.arange(400))],
     lambda: patched(data, "fit_reducer", reducer_fitted_on_everything)),
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ok = True
    for name, keyword, run_check, mutate in CASES:
        real_passes = not failed(run_check(), keyword)
        with mutate():
            caught = failed(run_check(), keyword)
        ok &= real_passes and caught
        print(f"[{'PASS' if real_passes and caught else 'FAIL'}] {name}: real code "
              f"{'passes' if real_passes else 'FAILS'}, mutant {'caught' if caught else 'MISSED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
