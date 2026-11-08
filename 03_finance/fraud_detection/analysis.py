"""Turn scored models into decisions: operating points, costs, uncertainty, stability.

Nothing here looks at the test set to choose anything. Thresholds come from validation;
the test set only reports what that choice would have cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from sklearn.svm import SVC

from config import (BOOTSTRAP_RESAMPLES, COST_RATIOS, QK_C, SEED, SHOT_LEVELS, SHOT_REPEATS,
                    Check, check)
from metrics import (Confusion, bootstrap_difference, cheapest_threshold, confusion, pr_auc,
                     recall_at_fpr)
from models import Scored, svm_scores
from quantum_kernel import kernel, shot_noise_kernel


@dataclass(frozen=True)
class Operating:
    model: Scored
    pr_auc: float
    recall_at_fpr: float
    threshold: float          # chosen on validation to minimise cost
    test: Confusion
    cost: float               # test-set cost at that threshold

    @property
    def label(self) -> str:
        return f"{self.model.name} ({self.model.regime})"


def operate(model: Scored, y_val: np.ndarray, y_test: np.ndarray, miss: float,
            review: float) -> Operating:
    threshold = cheapest_threshold(y_val, model.val, miss, review)
    test = confusion(y_test, model.test, threshold)
    return Operating(model, pr_auc(y_test, model.test), recall_at_fpr(y_test, model.test),
                     threshold, test, test.cost(miss, review))


def baseline_costs(y_test: np.ndarray, miss: float, review: float) -> dict[str, float]:
    frauds = int(y_test.sum())
    return {"Review nothing": miss * frauds, "Review everything": review * len(y_test)}


def cost_sensitivity(models: Sequence[Scored], y_val: np.ndarray, y_test: np.ndarray,
                     review: float) -> list[tuple[int, str, float]]:
    """For each miss/review ratio, the model with the lowest test cost (threshold from val)."""
    rows = []
    for ratio in COST_RATIOS:
        ops = [operate(m, y_val, y_test, ratio * review, review) for m in models]
        best = min(ops, key=lambda o: o.cost)
        rows.append((ratio, best.label, best.cost))
    return rows


@dataclass(frozen=True)
class Comparison:
    label: str
    difference: float
    low: float
    high: float

    @property
    def significant(self) -> bool:
        return self.low > 0 or self.high < 0


def compare(y_test: np.ndarray, a: Scored, b: Scored, label: str) -> Comparison:
    diff, low, high = bootstrap_difference(y_test, a.test, b.test, BOOTSTRAP_RESAMPLES)
    return Comparison(label, diff, low, high)


def stability(draws: Sequence[Sequence[Scored]], y_test: np.ndarray) -> dict[str, list[float]]:
    """Test PR-AUC of each fair-comparison model on every subsample draw."""
    out: dict[str, list[float]] = {}
    for models in draws:
        for m in models:
            out.setdefault(m.name, []).append(pr_auc(y_test, m.test))
    return out


@dataclass(frozen=True)
class ShotRow:
    shots: int
    pr_auc_mean: float
    pr_auc_low: float
    pr_auc_high: float
    shot_std_err: float        # typical shot error on one kernel entry


def kernel_spread(K: np.ndarray) -> float:
    """How much the training kernel entries differ from one another (off-diagonal std)."""
    return float(K[~np.eye(len(K), dtype=bool)].std())


def shot_noise_run(extra: dict, y_sub: np.ndarray, y_val: np.ndarray, y_test: np.ndarray,
                   shots: int, rng: np.random.Generator) -> float:
    """One hardware-like run: every kernel entry (train, validation, test) is estimated from
    `shots` measurements, C is re-tuned on the noisy validation kernel, then test is scored.

    The noisy training kernel is symmetrised with a unit diagonal (a device never needs to
    measure a transaction against itself). It is no longer exactly positive semidefinite;
    scikit-learn's solver still converges, as it would on real hardware estimates.
    """
    noisy = shot_noise_kernel(extra["K_train"], shots, rng)
    noisy = np.triu(noisy, 1) + np.triu(noisy, 1).T + np.eye(len(noisy))

    def noisy_kernel(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return shot_noise_kernel(kernel(a, b), shots, rng)

    best = None
    for c in QK_C:
        model = SVC(kernel="precomputed", C=c).fit(noisy, y_sub)
        val = pr_auc(y_val, svm_scores(model, extra["states_val"], extra["states_sub"],
                                       noisy_kernel))
        if best is None or val > best[0]:
            best = (val, model)
    return pr_auc(y_test, svm_scores(best[1], extra["states_test"], extra["states_sub"],
                                     noisy_kernel))


def shot_noise_test(quantum: Scored, y_sub: np.ndarray, y_val: np.ndarray,
                    y_test: np.ndarray) -> list[ShotRow]:
    """Shot noise only; hardware gate errors are not added. The bandwidth stays at its
    noiseless choice (re-encoding every transaction per bandwidth is the costly step), so
    this is the noise a model tuned on a simulator would meet, partly re-tuned."""
    extra = quantum.extra
    off = extra["K_train"][~np.eye(len(extra["K_train"]), dtype=bool)]
    rows = []
    for k, shots in enumerate(SHOT_LEVELS):
        aps = [shot_noise_run(extra, y_sub, y_val, y_test, shots,
                              np.random.default_rng([SEED, 41, k, rep]))
               for rep in range(SHOT_REPEATS)]
        std_err = float(np.sqrt(np.mean(off * (1 - off)) / shots))
        rows.append(ShotRow(shots, float(np.mean(aps)), min(aps), max(aps), std_err))
    return rows


def scorer_check(quantum: Scored) -> Check:
    """The chunked support-vector scorer must equal scikit-learn's own decision_function."""
    extra = quantum.extra
    rows = slice(0, 2_000)
    ours = svm_scores(extra["model"], extra["states_test"][rows], extra["states_sub"],
                      chunk_rows=300)                       # 7 chunks, the last one partial
    theirs = extra["model"].decision_function(kernel(extra["states_test"][rows],
                                                     extra["states_sub"]))
    gap = float(np.abs(ours - theirs).max())
    return check("Models: chunked quantum-SVM scorer equals scikit-learn's decision_function",
                 gap < 1e-9, f"max difference {gap:.1e} on 2,000 test rows in 7 chunks")
