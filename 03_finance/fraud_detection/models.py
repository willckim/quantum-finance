"""The six models, each tuned on validation PR-AUC and then scored on the untouched test set.

  Full data (classical only; a quantum kernel cannot be computed for 170,000 rows):
    Logistic regression      a weighted sum of all 30 features: the classic scorecard
    Gradient boosting        hundreds of small decision trees, each fixing the last's errors
  Fair comparison (identical 400-row balanced subsample, identical 6 PCA features):
    Logistic regression, gradient boosting, as above
    RBF SVM                  support vector machine with the standard classical kernel
    Quantum kernel SVM       the SAME support vector machine, with the quantum kernel
  The RBF SVM is the quantum model's closest classical twin: same algorithm, same data,
  only the similarity function differs. If the quantum kernel helped, it would show here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Callable, Iterable

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from config import (FULL_GB_GRID, LR_C, QK_BANDWIDTH, QK_C, QK_REPS, RBF_C, RBF_GAMMA, SEED,
                    SUB_GB_GRID)
from metrics import pr_auc
from quantum_kernel import feature_states, kernel

CHUNK_ROWS = 10_000


@dataclass(frozen=True)
class Scored:
    name: str
    regime: str                 # "full" or "subsample"
    params: dict
    val: np.ndarray             # scores on the validation rows (higher = more suspicious)
    test: np.ndarray            # scores on the test rows
    configs_tried: int
    extra: dict = field(default_factory=dict, compare=False)


def pick_best(name: str, regime: str, candidates: Iterable[tuple[dict, Callable]],
              y_val: np.ndarray) -> Scored:
    """Each candidate is (params, fit) where fit() returns (val scores, score_test, extra).

    Only validation PR-AUC decides. score_test() is called once, for the winner only, so
    the test set is scored after the choice is made and never influences it.
    """
    best, tried = None, 0
    for params, fit in candidates:
        val, score_test, extra = fit()
        tried += 1
        score = pr_auc(y_val, val)
        if best is None or score > best[0]:
            best = (score, params, val, score_test, extra)
    _, params, val, score_test, extra = best
    return Scored(name, regime, params, val, score_test(), tried, extra)


# ---- full data ---------------------------------------------------------------------------
def full_logistic(X, y, train, val, test) -> Scored:
    scaler = StandardScaler().fit(X[train])
    Z = {k: scaler.transform(X[rows]) for k, rows in (("tr", train), ("va", val), ("te", test))}

    def fit(c: float):
        model = LogisticRegression(C=c, max_iter=5_000).fit(Z["tr"], y[train])
        return model.decision_function(Z["va"]), lambda: model.decision_function(Z["te"]), {}

    return pick_best("Logistic regression", "full",
                     (({"C": c}, lambda c=c: fit(c)) for c in LR_C), y[val])


def full_gb_model(params: dict) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=300, early_stopping=False,
                                          l2_regularization=1.0, random_state=SEED, **params)


def full_boosting_without(X, y, train, test, params: dict, keep: list[int]) -> np.ndarray:
    """Refit the tuned full-data model on a subset of columns; return its test scores."""
    model = full_gb_model(params).fit(X[train][:, keep], y[train])
    return model.predict_proba(X[test][:, keep])[:, 1]


def full_boosting(X, y, train, val, test) -> Scored:
    def fit(params: dict):
        model = full_gb_model(params).fit(X[train], y[train])
        return model.predict_proba(X[val])[:, 1], lambda: model.predict_proba(X[test])[:, 1], {}

    return pick_best("Gradient boosting", "full",
                     ((p, lambda p=p: fit(p)) for p in FULL_GB_GRID), y[val])


# ---- fair comparison on the subsample --------------------------------------------------------
@dataclass(frozen=True)
class FairData:
    """The one subsample and feature set every fair-comparison model receives."""

    F_sub: np.ndarray
    y_sub: np.ndarray
    F_val: np.ndarray
    F_test: np.ndarray


def sub_logistic(d: FairData, y_val: np.ndarray) -> Scored:
    def fit(c: float):
        model = LogisticRegression(C=c, max_iter=5_000).fit(d.F_sub, d.y_sub)
        return model.decision_function(d.F_val), lambda: model.decision_function(d.F_test), {}

    return pick_best("Logistic regression", "subsample",
                     (({"C": c}, lambda c=c: fit(c)) for c in LR_C), y_val)


def sub_boosting(d: FairData, y_val: np.ndarray) -> Scored:
    def fit(params: dict):
        model = HistGradientBoostingClassifier(max_iter=200, early_stopping=False,
                                               random_state=SEED, **params).fit(d.F_sub, d.y_sub)
        return model.predict_proba(d.F_val)[:, 1], lambda: model.predict_proba(d.F_test)[:, 1], {}

    return pick_best("Gradient boosting", "subsample",
                     ((p, lambda p=p: fit(p)) for p in SUB_GB_GRID), y_val)


def sub_rbf_svm(d: FairData, y_val: np.ndarray) -> Scored:
    def fit(gamma: float, c: float):
        model = SVC(kernel="rbf", gamma=gamma, C=c).fit(d.F_sub, d.y_sub)
        return model.decision_function(d.F_val), lambda: model.decision_function(d.F_test), {}

    grid = (({"gamma": float(g), "C": c}, lambda g=g, c=c: fit(g, c))
            for g, c in product(RBF_GAMMA, RBF_C))
    return pick_best("RBF SVM", "subsample", grid, y_val)


def svm_scores(model: SVC, states_eval: np.ndarray, states_train: np.ndarray,
               kernel_fn: Callable[[np.ndarray, np.ndarray], np.ndarray] = kernel,
               chunk_rows: int = CHUNK_ROWS) -> np.ndarray:
    """decision_function for a precomputed-kernel SVM, computed against the support vectors
    only and in chunks, so the 57,000 x 400 test kernel never sits in memory at once."""
    support = states_train[model.support_]
    coef, intercept = model.dual_coef_[0], model.intercept_[0]
    return np.concatenate([kernel_fn(states_eval[i:i + chunk_rows], support) @ coef + intercept
                           for i in range(0, len(states_eval), chunk_rows)])


def sub_quantum_svm(d: FairData, y_val: np.ndarray) -> Scored:
    cache: dict[tuple[int, float], tuple] = {}

    def states(reps: int, bw: float):
        if (reps, bw) not in cache:
            cache.clear()                                    # keep one setting in memory
            s_sub, s_val, s_test = (feature_states(bw * F, reps)
                                    for F in (d.F_sub, d.F_val, d.F_test))
            cache[(reps, bw)] = (s_sub, s_val, s_test, kernel(s_sub, s_sub))
        return cache[(reps, bw)]

    def fit(reps: int, bw: float, c: float):
        s_sub, s_val, s_test, K = states(reps, bw)
        model = SVC(kernel="precomputed", C=c).fit(K, d.y_sub)
        extra = {"model": model, "K_train": K, "states_sub": s_sub, "states_test": s_test,
                 "states_val": s_val}
        return svm_scores(model, s_val, s_sub), lambda: svm_scores(model, s_test, s_sub), extra

    grid = (({"reps": r, "bandwidth": b, "C": c}, lambda r=r, b=b, c=c: fit(r, b, c))
            for r, b, c in product(QK_REPS, QK_BANDWIDTH, QK_C))
    return pick_best("Quantum kernel SVM", "subsample", grid, y_val)


GRIDS = {
    ("Logistic regression", "full"): {"C": LR_C},
    ("Gradient boosting", "full"): {k: sorted({p[k] for p in FULL_GB_GRID})
                                    for k in FULL_GB_GRID[0]},
    ("Logistic regression", "subsample"): {"C": LR_C},
    ("Gradient boosting", "subsample"): {k: sorted({p[k] for p in SUB_GB_GRID})
                                         for k in SUB_GB_GRID[0]},
    ("RBF SVM", "subsample"): {"gamma": RBF_GAMMA, "C": RBF_C},
    ("Quantum kernel SVM", "subsample"): {"reps": QK_REPS, "bandwidth": QK_BANDWIDTH, "C": QK_C},
}


def edge_hits(model: Scored) -> list[str]:
    """Tuned values that sit at the end of their grid: the best may lie beyond the search."""
    hits = []
    for key, values in GRIDS[(model.name, model.regime)].items():
        values = sorted(values)
        if len(values) > 2 and model.params[key] in (values[0], values[-1]):
            end = "smallest" if model.params[key] == values[0] else "largest"
            hits.append(f"{model.name} ({model.regime}): {key} = {model.params[key]:g} is the "
                        f"{end} value tried")
    return hits


def fair_models(d: FairData, y_val: np.ndarray) -> list[Scored]:
    return [sub_logistic(d, y_val), sub_boosting(d, y_val), sub_rbf_svm(d, y_val),
            sub_quantum_svm(d, y_val)]
