"""How the models are graded, and why accuracy is not on the list.

WHY ACCURACY IS MISLEADING HERE
  Only 0.17% of transactions are fraud. A "model" that approves EVERY transaction is right
  99.83% of the time and catches no fraud at all. Any model that flags even a few hundred
  legitimate payments can score LOWER accuracy than that do-nothing model while being far
  more useful. Accuracy rewards agreeing with the majority, and in fraud work the majority
  is the part nobody needs help with. accuracy_paradox_check() demonstrates it on the
  test set.

WHAT IS REPORTED INSTEAD
  * PR-AUC (average precision): area under the precision-recall curve. Precision = of the
    alerts raised, the share that were fraud (an analyst's hit rate). Recall = of the
    frauds, the share that raised an alert. A random model scores the fraud rate (0.0017),
    a perfect one 1.0. It ignores the mass of correctly ignored legitimate payments, which
    is the right thing to ignore.
  * Recall at a fixed false-positive rate: how much fraud is caught if the alert queue may
    contain at most 1 false alarm per 1,000 legitimate transactions. This mirrors the real
    constraint: an investigations team has a fixed capacity.
  * Confusion matrix at an operating threshold, and the cost view below.

THE COST VIEW
  total cost = miss cost x (frauds not flagged) + review cost x (alerts raised)
  Every alert is reviewed, fraud or not, so a true alert also costs a review. Each model's
  threshold is chosen to minimise this cost on the VALIDATION set, then the cost is
  reported on the TEST set, the way a team would set a threshold and then live with it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score, roc_curve

from config import FPR_TARGET, SEED, Check, check


def pr_auc(y: np.ndarray, scores: np.ndarray) -> float:
    return float(average_precision_score(y, scores))


def recall_at_fpr(y: np.ndarray, scores: np.ndarray, fpr_target: float = FPR_TARGET) -> float:
    """Highest recall reachable while the false-positive rate stays at or below the target.

    GOTCHA: roc_curve drops "intermediate" points by default, which can delete the very
    operating point that meets the target when scores tie. Keep every point.
    """
    fpr, tpr, _ = roc_curve(y, scores, drop_intermediate=False)
    return float(tpr[fpr <= fpr_target + 1e-12].max())


@dataclass(frozen=True)
class Confusion:
    tp: int      # fraud, flagged
    fp: int      # legitimate, flagged (a false alarm)
    fn: int      # fraud, missed
    tn: int      # legitimate, left alone

    def cost(self, miss_cost: float, review_cost: float) -> float:
        return miss_cost * self.fn + review_cost * (self.tp + self.fp)

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / (self.tp + self.fp + self.fn + self.tn)


def confusion(y: np.ndarray, scores: np.ndarray, threshold: float) -> Confusion:
    flagged = scores >= threshold
    fraud = y == 1
    return Confusion(int((flagged & fraud).sum()), int((flagged & ~fraud).sum()),
                     int((~flagged & fraud).sum()), int((~flagged & ~fraud).sum()))


def cheapest_threshold(y: np.ndarray, scores: np.ndarray, miss_cost: float,
                       review_cost: float) -> float:
    """Threshold minimising total cost on (y, scores). Candidates include 'flag nothing'
    (+inf) and every distinct score, so 'flag everything' is covered too."""
    order = np.argsort(-scores, kind="stable")
    s, fraud = scores[order], (y[order] == 1)
    alerts = np.arange(1, len(s) + 1)                    # flag the top k transactions
    missed = fraud.sum() - np.cumsum(fraud)
    cost = miss_cost * missed + review_cost * alerts
    last_of_tie = np.r_[s[1:] != s[:-1], True]           # a threshold cannot split ties
    cost = np.where(last_of_tie, cost, np.inf)
    best = int(np.argmin(cost))
    if miss_cost * fraud.sum() <= cost[best]:
        return float("inf")
    return float(s[best])


def bootstrap_difference(y: np.ndarray, a: np.ndarray, b: np.ndarray, resamples: int,
                         metric=pr_auc) -> tuple[float, float, float]:
    """Paired bootstrap of metric(a) - metric(b) on the same resampled test rows.

    Frauds and legitimate rows are resampled separately so every replicate keeps the
    test set's fraud count. Returns (observed difference, 2.5th, 97.5th percentile).
    """
    rng = np.random.default_rng([SEED, 97])
    pos, neg = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    diffs = np.empty(resamples)
    for r in range(resamples):
        rows = np.concatenate([rng.choice(pos, pos.size), rng.choice(neg, neg.size)])
        diffs[r] = metric(y[rows], a[rows]) - metric(y[rows], b[rows])
    low, high = np.percentile(diffs, [2.5, 97.5])
    return metric(y, a) - metric(y, b), float(low), float(high)


# ---- answer key, worked by hand -----------------------------------------------------------
# Ten transactions, scores 10 (most suspicious) down to 1. Frauds at scores 10, 8 and 3.
#   Ranked: 10 F, 9 L, 8 F, 7 L, 6 L, 5 L, 4 L, 3 F, 2 L, 1 L   (3 frauds, 7 legitimate)
#   Average precision = mean of precision at each fraud = (1/1 + 2/3 + 3/8) / 3 = 0.680556
#   Recall at FPR <= 1/7: flag 10, 9, 8 -> 2 of 3 frauds, 1 false alarm = 1/7 -> 0.666667
#   Threshold 8, miss 100, review 10: flag 3, catch 2, miss 1 -> 100 + 30 = 130
#   Cheapest threshold at miss 100, review 10: flag top 8 (all frauds) costs 80; top 3
#   costs 130; top 1 costs 210; nothing costs 300 -> threshold 3, cost 80
#   Always-legit: accuracy 7/10, recall 0
# Ties: ten pairs, each one fraud and one legitimate transaction with the SAME score.
#   Flagging the top 5 pairs catches 5 of 10 frauds with 5 of 10 false alarms, so recall
#   at FPR <= 0.5 is exactly 0.5 (no threshold can split a tied pair).
TOY_SCORES = np.arange(10, 0, -1, dtype=float)
TOY_LABELS = np.array([1, 0, 1, 0, 0, 0, 0, 1, 0, 0])
TIED_SCORES = np.repeat(np.arange(10, 0, -1, dtype=float), 2)
TIED_LABELS = np.tile([1, 0], 10)


def metric_answer_key_checks() -> list[Check]:
    ap = pr_auc(TOY_LABELS, TOY_SCORES)
    rec = recall_at_fpr(TOY_LABELS, TOY_SCORES, 1 / 7)
    tied = recall_at_fpr(TIED_LABELS, TIED_SCORES, 0.5)
    at8 = confusion(TOY_LABELS, TOY_SCORES, 8.0)
    best = cheapest_threshold(TOY_LABELS, TOY_SCORES, 100.0, 10.0)
    best_cost = confusion(TOY_LABELS, TOY_SCORES, best).cost(100.0, 10.0)
    never = confusion(TOY_LABELS, TOY_SCORES, np.inf)
    lower = cheapest_threshold(TOY_LABELS, TOY_SCORES, 1.0, 10.0)   # reviews dearer than misses
    return [
        check("Metrics: PR-AUC on the hand-worked example = 0.680556", abs(ap - 0.680556) < 1e-6,
              f"{ap:.6f}"),
        check("Metrics: recall at FPR <= 1/7 on the hand-worked example = 2/3",
              abs(rec - 2 / 3) < 1e-9, f"{rec:.6f}"),
        check("Metrics: recall at FPR <= 0.5 with every score tied in pairs = 0.5",
              abs(tied - 0.5) < 1e-9, f"{tied:.6f}"),
        check("Metrics: confusion and cost at threshold 8 = (2, 1, 1, 6) and 130",
              (at8.tp, at8.fp, at8.fn, at8.tn) == (2, 1, 1, 6) and at8.cost(100, 10) == 130),
        check("Metrics: cheapest threshold = 3 at cost 80; flags nothing when reviews cost "
              "more than misses", best == 3.0 and best_cost == 80 and lower == float("inf"),
              f"threshold {best}, cost {best_cost}"),
        check("Metrics: always-legit scores 70% accuracy and 0 recall on the example",
              never.accuracy == 0.7 and never.tp == 0),
    ]


def accuracy_paradox_check(y_test: np.ndarray, best: Confusion, best_name: str) -> Check:
    """Accuracy cannot tell 'catches most fraud' from 'catches none' on this data."""
    lazy = confusion(y_test, np.zeros(len(y_test)), np.inf)
    best_recall = best.tp / (best.tp + best.fn)
    gap = best.accuracy - lazy.accuracy
    return check("Metrics: accuracy separates the best model from approve-everything by under "
                 "0.2 points, while recall goes from 0 to over half",
                 abs(gap) < 0.002 and lazy.tp == 0 and best_recall > 0.5,
                 f"accuracy {lazy.accuracy:.3%} vs {best.accuracy:.3%}; "
                 f"recall 0% vs {best_recall:.0%} ({best_name})")
