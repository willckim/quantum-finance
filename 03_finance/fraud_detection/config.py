"""Run-wide settings for the fraud detection comparison, and the shared Check record.

Every number a reader might want to change lives here: the split, the subsample size,
the qubit count, the tuning grids, the fixed false-positive rate, and the two costs.
The costs can also be set per run: py run_all.py --miss-cost 500 --review-cost 15
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

VERSION = "1.0.0"
SEED = 2026
HERE = Path(__file__).resolve().parent

# ----------------------------- data ------------------------------------------------
# ULB / Worldline credit card fraud data, OpenML mirror (dataset 1597, no login needed).
# The expected values come from the publishers, not from this code: OpenML's published
# MD5 for the file, and the dataset description's "492 frauds out of 284,807".
DATA_URL = "https://www.openml.org/data/v1/download/1673544/creditcard.arff"
DATA_PATH = HERE / "data" / "creditcard.arff"        # gitignored; fetched by download_data.py
DATA_MD5 = "178bcf9bb1f31a3dfe12d0e577884add"
EXPECTED_ROWS, EXPECTED_FRAUDS, EXPECTED_FEATURES = 284_807, 492, 30
DATA_NOTICE = (
    "Contains data from the Credit Card Fraud Detection dataset (Worldline and the Machine "
    "Learning Group of ULB), made available under the Open Database License (ODbL) v1.0; "
    "contents under the Database Contents License (DbCL) v1.0.")
DATA_CITATION = (
    "Dal Pozzolo, Caelen, Johnson and Bontempi, 'Calibrating Probability with Undersampling "
    "for Unbalanced Classification', IEEE SSCI 2015.")

# ----------------------------- splits and the fair subsample -------------------------
VAL_FRACTION, TEST_FRACTION = 0.2, 0.2   # stratified: every split keeps the 0.17% fraud rate
SUBSAMPLE_PER_CLASS = 200                # 200 frauds + 200 legitimate, drawn from TRAIN only
N_QUBITS = 6                             # features after PCA = qubits in the feature map
N_SUBSAMPLE_DRAWS = 5                    # repeat the whole fair comparison on fresh draws

# ----------------------------- tuning grids (chosen on validation PR-AUC) ------------
# The two kernel methods get the SAME budget, 36 configurations each, so neither wins by
# being searched harder.
QK_REPS = (1, 2)                                     # feature-map repetitions
QK_BANDWIDTH = (0.002, 0.005, 0.01, 0.02, 0.05, 0.1)  # features are scaled by this first
QK_C = (10.0, 100.0, 1000.0)
RBF_GAMMA = tuple(float(g) for g in np.geomspace(1e-4, 1.0, 9))
RBF_C = (1.0, 10.0, 100.0, 1000.0)
LR_C = (1e-4, 1e-3, 0.01, 0.1, 1.0, 10.0, 100.0)
SUB_GB_GRID = tuple({"max_depth": d, "learning_rate": lr, "l2_regularization": l2}
                    for d in (2, 3) for lr in (0.05, 0.1) for l2 in (0.0, 1.0))
FULL_GB_GRID = tuple({"learning_rate": lr, "max_leaf_nodes": leaves}
                     for lr in (0.05, 0.1) for leaves in (15, 31))

# ----------------------------- metrics and costs --------------------------------------
FPR_TARGET = 0.001       # recall is read at 1 false alarm per 1,000 legitimate transactions
# ILLUSTRATIVE costs, not sourced: replace with your own. A missed fraud costs the loss
# plus chargeback and investigation overhead (the mean fraud in this data is about 122
# euros). Every alert, fraud or not, costs one analyst review.
MISS_COST = 300.0
REVIEW_COST = 20.0
COST_RATIOS = (5, 15, 50, 150)   # miss cost / review cost, for the sensitivity table
BOOTSTRAP_RESAMPLES = 1000

# ----------------------------- quantum hardware stress test --------------------------
SHOT_LEVELS = (1_024, 16_384, 262_144)    # shots per kernel entry
SHOT_REPEATS = 3                          # fresh noise draws per level, C re-tuned each time
AER_SHOTS, AER_PAIRS = 4_096, 20          # real compute-uncompute circuits on Aer
Z_SCORE_LIMIT = 4.0

# ----------------------------- outputs --------------------------------------------------
PR_CHART = HERE / "fraud_pr_curves.png"
REPORT_MD = HERE / "fraud_report.md"
SUMMARY_CSV = HERE / "fraud_summary.csv"
RUN_LOG = HERE / "fraud_run_log.json"


def validate_settings() -> None:
    """Refuse settings that would crash midway or quietly exhaust memory."""
    if not 2 <= N_QUBITS <= 10:
        raise ValueError(f"N_QUBITS = {N_QUBITS}: use 2 to 10 (statevectors for 114,000 "
                         "transactions grow as 2^N_QUBITS)")
    if SUBSAMPLE_PER_CLASS < 10 or N_SUBSAMPLE_DRAWS < 1:
        raise ValueError("SUBSAMPLE_PER_CLASS must be at least 10 and N_SUBSAMPLE_DRAWS >= 1")


@dataclass(frozen=True)
class Check:
    """One self-verification result. NOT RUN means the check could not see its subject."""

    name: str
    status: str            # "PASS", "FAIL" or "NOT RUN"
    detail: str = ""


def check(name: str, passed: bool, detail: str = "") -> Check:
    return Check(name, "PASS" if passed else "FAIL", detail)


def section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)
