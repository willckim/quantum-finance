"""Precision-recall curves for every model, written to fraud_pr_curves.png."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib
matplotlib.use("Agg")  # write the PNG without needing a display
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import precision_recall_curve

from config import DATA_NOTICE, N_QUBITS, SUBSAMPLE_PER_CLASS
from metrics import pr_auc
from models import Scored

INK, INK_SECONDARY, INK_MUTED = "#0b0b0b", "#52514e", "#898781"
CHART_SURFACE, GRIDLINE, AXIS_LINE = "#fcfcfb", "#e1e0d9", "#c3c2b7"
# Categorical slots 1-4 of a palette validated for colour-vision deficiency. Colour follows
# the MODEL, so logistic regression is blue in both panels. Low-contrast hues are always
# named in the legend with their PR-AUC, never identified by colour alone.
MODEL_COLOR = {"Logistic regression": "#2a78d6", "Gradient boosting": "#eb6834",
               "RBF SVM": "#1baf7a", "Quantum kernel SVM": "#eda100"}


def style(ax, title: str, prevalence: float) -> None:
    ax.set_facecolor(CHART_SURFACE)
    ax.grid(True, color=GRIDLINE, linewidth=0.8)
    ax.set_axisbelow(True)
    for side, spine in ax.spines.items():
        spine.set_color(AXIS_LINE)
        spine.set_visible(side in ("left", "bottom"))
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)
    ax.set_title(title, loc="left", fontsize=11.5, color=INK, pad=10)
    ax.set_xlabel("Recall: share of fraud caught", color=INK_SECONDARY, fontsize=10)
    ax.set_ylabel("Precision: share of alerts that are fraud", color=INK_SECONDARY, fontsize=10)
    ax.axhline(prevalence, color=INK_MUTED, lw=1, ls=":")
    ax.text(0.01, prevalence + 0.015, f"random guessing ({prevalence:.2%} of transactions are "
            "fraud)", fontsize=8.5, color=INK_MUTED)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.02)


def draw(ax, y: np.ndarray, model: Scored, dashed: bool = False, suffix: str = "") -> None:
    precision, recall, _ = precision_recall_curve(y, model.test)
    ax.plot(recall, precision, "--" if dashed else "-", color=MODEL_COLOR[model.name], lw=2,
            drawstyle="steps-post",
            label=f"{model.name}{suffix}  (PR-AUC {pr_auc(y, model.test):.2f})")


def plot_pr_curves(y_test: np.ndarray, full: Sequence[Scored], fair: Sequence[Scored],
                   path: Path) -> None:
    prevalence = float(y_test.mean())
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(14, 7.2), dpi=160)
    fig.patch.set_facecolor(CHART_SURFACE)
    style(ax_a, f"A.  Fair comparison: same {2 * SUBSAMPLE_PER_CLASS} training rows, same "
          f"{N_QUBITS} features", prevalence)
    style(ax_b, "B.  Classical models on the full training set (all 30 features)", prevalence)
    for model in fair:
        draw(ax_a, y_test, model)
    for model in full:
        draw(ax_b, y_test, model)
    quantum = next(m for m in fair if m.name == "Quantum kernel SVM")
    draw(ax_b, y_test, quantum, dashed=True, suffix=" from A, for reference")
    for ax in (ax_a, ax_b):
        ax.legend(loc="lower left", bbox_to_anchor=(0.0, 0.06), frameon=False, fontsize=9,
                  labelcolor=INK_SECONDARY)
    fig.suptitle("Credit card fraud: precision-recall on the same held-out test set",
                 x=0.055, y=0.975, ha="left", fontsize=15, color=INK)
    fig.text(0.055, 0.925,
             f"Test set: {len(y_test):,} transactions, {int(y_test.sum())} frauds, never used "
             "for training or tuning. Higher and further right is better.\n"
             "Quantum kernel computed by exact statevector simulation (no shot noise, no "
             "hardware errors): a best case for the quantum model.",
             fontsize=8.5, color=INK_SECONDARY, va="top", linespacing=1.5)
    fig.text(0.055, 0.012, DATA_NOTICE, fontsize=7.5, color=INK_MUTED, va="bottom")
    fig.subplots_adjust(left=0.055, right=0.985, top=0.82, bottom=0.11, wspace=0.18)
    fig.savefig(path, dpi=160, facecolor=CHART_SURFACE)
    plt.close(fig)
