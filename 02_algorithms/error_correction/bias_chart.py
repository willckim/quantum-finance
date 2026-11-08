"""Part 7 chart: logical error rate vs bias ratio for each code, written to biased_noise.png.

Part of the error-correction walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # write the PNG without needing a display
import matplotlib.pyplot as plt
import numpy as np

from biased_noise import BiasResults, crossover_eta, rep_key
from noise_and_tools import (BIAS_PNG, BIAS_RATIOS, P_BIAS, REP_LENGTHS, SHOTS,
                             SURFACE_D5_SAMPLES, Check)
from threshold_sweep import (AXIS_LINE, CHART_SURFACE, GRIDLINE, INK, INK_MUTED, INK_SECONDARY,
                             SERIES_COLOR, confidence_halfwidth)

# Repetition codes are one ordered family, so they share one hue, darker = longer (an ordinal
# ramp, validated with the dataviz palette checker). The surface codes keep Part 6's colours.
REP_RAMP = ("#6da7ec", "#2a78d6", "#184f95")
CHART_SUBTITLE = (
    f"Code-capacity noise at a fixed total error rate p = {P_BIAS:.0%} per data qubit: errors hit "
    "the data qubits once; checks, corrections and readout are perfect.\n"
    "Bias ratio = P(Z) / P(X), with P(Y) = P(X); 1 is depolarizing noise.  Lines: exact "
    "enumeration.  Dots: Qiskit Aer circuits (95% intervals).\n"
    "Surface d=5: Pauli-frame sampling.  Real hardware also needs bias-preserving gates, "
    "which cat qubits provide and ordinary qubits do not.")


def rep_color(n: int) -> str:
    return REP_RAMP[min(REP_LENGTHS.index(n), len(REP_RAMP) - 1)]


def style_bias_axes(ax) -> None:
    ax.set_facecolor(CHART_SURFACE)
    ax.grid(True, which="major", color=GRIDLINE, linewidth=0.8)
    ax.set_axisbelow(True)
    for side, spine in ax.spines.items():
        spine.set_color(AXIS_LINE)
        spine.set_visible(side in ("left", "bottom"))
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)
    ax.set_xlabel("Bias ratio  P(phase flip) / P(bit flip)   (log scale)", color=INK_SECONDARY,
                  fontsize=10)
    ax.set_ylabel(f"Logical error rate at p = {P_BIAS:.0%}   (log scale)", color=INK_SECONDARY,
                  fontsize=10)


def end_label(ax, x: float, y: float, text: str) -> None:
    ax.annotate(text, xy=(x, y), xytext=(6, 0), textcoords="offset points", va="center",
                fontsize=9, color=INK)


def plot_bias_chart(results: BiasResults, path: Path = BIAS_PNG) -> None:
    etas = np.asarray(BIAS_RATIOS, dtype=float)
    dense = np.geomspace(etas[0], etas[-1], 200)
    fig, ax = plt.subplots(figsize=(12, 7.4), dpi=160)
    fig.patch.set_facecolor(CHART_SURFACE)
    style_bias_axes(ax)
    ax.set_xscale("log")
    ax.set_yscale("log")

    ax.plot(dense, np.full_like(dense, P_BIAS), color=INK_SECONDARY, lw=1.6,
            label="Unencoded qubit (1 qubit)")
    end_label(ax, dense[-1], P_BIAS, "bare qubit")
    series = [(rep_key(n), rep_color(n), "-", f"Phase-flip repetition n={n}") for n in REP_LENGTHS]
    series.append(("surface3", SERIES_COLOR["surface3"], "--", "Surface code d=3"))
    for key, color, style, name in series:
        qubits = results.qubits[key]
        curve = [results.exact[key](float(e)) for e in dense]
        ax.plot(dense, curve, style, color=color, lw=2, label=f"{name} ({qubits} qubits)")
        rates = np.asarray(results.circuit[key])
        ax.errorbar(etas, rates, yerr=confidence_halfwidth(rates, SHOTS), fmt="o", ms=6,
                    color=color, mec=CHART_SURFACE, mew=1.2, elinewidth=1, zorder=4)
        end_label(ax, dense[-1], curve[-1], f"{name.split()[-1]}  ({qubits}q)")
    d5 = np.asarray(results.surface5)
    ax.errorbar(etas, d5, yerr=confidence_halfwidth(d5, SURFACE_D5_SAMPLES), fmt="s--", ms=6,
                color=SERIES_COLOR["surface5"], lw=2, mec=CHART_SURFACE, mew=1.2, elinewidth=1,
                label=f"Surface code d=5 ({results.qubits['surface5']} qubits), sampled")
    end_label(ax, etas[-1], d5[-1], f"d=5  ({results.qubits['surface5']}q)")

    short = REP_LENGTHS[0]
    cross = crossover_eta(results.exact[rep_key(short)], results.exact["surface3"], etas[-1])
    if cross is not None:
        y = results.exact["surface3"](cross)
        ax.plot([cross], [y], "o", ms=11, mfc="none", mec=INK, mew=1.3, zorder=5)
        ax.annotate(f"past bias ~{cross:.0f}, the {results.qubits[rep_key(short)]}-qubit code\n"
                    f"beats the {results.qubits['surface3']}-qubit surface code",
                    xy=(cross, y), xytext=(cross * 0.32, y * 0.18), fontsize=9, color=INK,
                    arrowprops={"arrowstyle": "-", "color": INK_MUTED, "lw": 0.8})
    ax.text(etas[-1], P_BIAS * 1.12, "above this line, the code is worse than no code",
            ha="right", fontsize=9, color=INK_MUTED)
    ax.set_xlim(etas[0] * 0.85, etas[-1] * 2.6)
    positive = [r for rates in results.circuit.values() for r in rates if r > 0]
    ax.set_ylim(min(positive, default=1e-4) * 0.2, 0.4)
    ax.legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
    fig.suptitle("Biased noise: when phase flips dominate, a line of qubits beats a grid",
                 x=0.07, y=0.975, ha="left", fontsize=15, color=INK)
    fig.text(0.07, 0.925, CHART_SUBTITLE, fontsize=9, color=INK_SECONDARY, va="top",
             linespacing=1.5)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.80, bottom=0.09)
    fig.savefig(path, dpi=160, facecolor=CHART_SURFACE)
    plt.close(fig)


def part7_chart(results: BiasResults) -> list[Check]:
    plot_bias_chart(results, BIAS_PNG)
    print(f"\nChart written to {BIAS_PNG}")
    return [Check("Part 7: bias chart file written",
                  BIAS_PNG.exists() and BIAS_PNG.stat().st_size > 20_000,
                  f"{BIAS_PNG.stat().st_size:,} bytes" if BIAS_PNG.exists() else "missing")]
