"""Part 6 -- the threshold chart, the overhead arithmetic, and what the numbers mean.

Part of the error-correction walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

import matplotlib
matplotlib.use("Agg")  # write the PNG without needing a display
import matplotlib.pyplot as plt
import numpy as np

from noise_and_tools import (OUTPUT_PNG, P_SWEEP_EXACT, P_SWEEP_LOG, SURFACE_D5_SAMPLES, Check,
                             Sweep, percent, section)
from pauli_frame import break_even_point


# ======================================================================================
# PART 6 -- THE THRESHOLD CHART, AND WHY ANY OF THIS MATTERS
# ======================================================================================
# READING THE CHART
#   Panel A asks whether ONE encoded qubit beats a bare one. Every code line starts under
#   the diagonal (failures need two errors, so they scale like p^2) and eventually crosses
#   it: past that break-even point the extra qubits bring in more errors than the code can
#   remove. The 3-qubit code under depolarizing noise never gets under the line at all.
#
#   Panel B asks the question that decides whether quantum computing scales: does a BIGGER
#   code help? Below the crossing of the d=3 and d=5 curves, the 49-qubit code is better
#   than the 17-qubit one; above it, worse. That crossing is the THRESHOLD. Below it, each
#   step of 2 in distance multiplies the protection by roughly p_threshold / p, so the
#   logical error rate can be pushed as low as needed just by adding qubits. Above it,
#   adding qubits makes things worse forever. Everything in fault tolerance hinges on
#   getting hardware below that line and then as far below it as possible.
#
# WHY THESE THRESHOLDS ARE HIGHER THAN THE "~1%" YOU READ ABOUT
#   This chart is code-capacity noise (perfect checks), which is why its surface-code
#   threshold lands near 13%. Real syndrome extraction uses
#   noisy ancillas, CNOTs and measurements, and hardware repeats the checks every cycle
#   to catch lying syndromes. Under that circuit-level noise the surface code threshold is
#   about 1% (Wang, Fowler & Hollenberg 2011 report just over 1%; Fowler et al. 2012 report
#   about 0.57% under a stricter noise model).
#   The shape of the story, crossing curves and p^((d+1)/2) suppression, is the same.
#
# WHAT "70 LOGICAL QUBITS" DOES AND DOES NOT MEAN
#   In mid-2026 IBM and the University of Chicago reported a sampling computation on about
#   70 logical qubits (arXiv:2607.25941), with effective error rates roughly 10x below the
#   physical ones. The fine print matters: those qubits used error-DETECTING spacetime
#   codes, and runs in which a check fired were discarded (post-selection). Detection is
#   the rung below correction. It works for sampling experiments, but the fraction of runs
#   that survive shrinks exponentially as circuits grow, so it cannot carry a long
#   computation. Every code in this script CORRECTS errors, which is what removes that
#   ceiling. "Logical qubit" now covers very different levels of protection, so the useful
#   questions are always: detected or corrected, at what distance, and with what logical
#   error rate per operation?
#
# THE OVERHEAD, WHICH IS THE REAL ENGINEERING PROBLEM
#   A distance-d rotated surface code uses d^2 data + (d^2 - 1) measure qubits. Useful
#   algorithms (chemistry, factoring) need logical error rates near 1e-12 per operation.
#   With the usual rule of thumb P_L ~ 0.1 * (p / 1%)^((d+1)/2), where 1% is the
#   circuit-level threshold (not this script's 13% code-capacity figure), hardware at p = 0.1%
#   needs d = 21, which is 881 physical qubits for EVERY logical qubit. Improving the
#   physical error rate from 0.2% to 0.1% cuts that from 1,921 to 881: qubit quality
#   matters as much as qubit count. It is also why IBM's roadmap moved to qLDPC "bivariate
#   bicycle" codes: the [[144,12,12]] gross code (Bravyi et al., Nature 2024) protects 12
#   logical qubits with 288 physical qubits, where the authors estimate a surface code
#   would need nearly 3,000 for comparable protection at p = 0.1%, about 10x more. The
#   price is long-range couplers the surface code avoids.

INK, INK_SECONDARY, INK_MUTED = "#0b0b0b", "#52514e", "#898781"
CHART_SURFACE, GRIDLINE, AXIS_LINE, HELP_REGION = "#fcfcfb", "#e1e0d9", "#c3c2b7", "#f0efec"
# Categorical slots 1-4 of a palette validated for colour-vision deficiency (adjacent pairs).
SERIES_COLOR = {"rep3": "#2a78d6", "shor": "#eb6834", "surface3": "#1baf7a", "surface5": "#eda100"}
CHART_SUBTITLE = (
    "Code-capacity noise: errors hit the data qubits once; checks, corrections and readout "
    "are perfect.\n"
    "Lines: exact enumeration of every error pattern.  Dots: full Qiskit Aer circuits (A) or "
    "Pauli-frame sampling (B), with 95% intervals.\n"
    "Noisy checks on real hardware pull these numbers far lower: the surface-code threshold "
    "drops to about 1%.")


@dataclass(frozen=True)
class ThresholdAnalysis:
    break_even: Mapping[str, float | None]          # read-only view (MappingProxyType)
    d5_break_even: float | None
    threshold: float | None


def interpolate_crossing(ps: Sequence[float], upper: Sequence[float],
                         lower: Sequence[float]) -> float | None:
    """First p where `upper` rises through `lower`, interpolated linearly in log p."""
    diff = np.asarray(upper, dtype=float) - np.asarray(lower, dtype=float)
    for k in range(len(ps) - 1):
        if diff[k] < 0 <= diff[k + 1]:
            t = diff[k] / (diff[k] - diff[k + 1])
            return float(np.exp(np.log(ps[k]) + t * (np.log(ps[k + 1]) - np.log(ps[k]))))
    return None


def confidence_halfwidth(rates: Sequence[float], shots: int) -> np.ndarray:
    r = np.asarray(rates, dtype=float)
    return 1.96 * np.sqrt(r * (1.0 - r) / shots)


def style_axes(ax, title: str) -> None:
    ax.set_facecolor(CHART_SURFACE)
    ax.grid(True, which="major", color=GRIDLINE, linewidth=0.8)
    ax.set_axisbelow(True)
    for side, spine in ax.spines.items():
        spine.set_color(AXIS_LINE)
        spine.set_visible(side in ("left", "bottom"))
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)
    ax.set_title(title, loc="left", fontsize=11.5, color=INK, pad=10)
    ax.set_xlabel("Physical error rate p (per qubit)", color=INK_SECONDARY, fontsize=10)
    ax.set_ylabel("Logical error rate", color=INK_SECONDARY, fontsize=10)


def draw_break_even_panel(ax, curves: dict[str, Callable[[float], float]],
                          sweeps: dict[str, Sweep], break_even: Mapping[str, float | None]) -> None:
    p = P_SWEEP_EXACT
    ax.fill_between(p, 0, p, color=HELP_REGION, linewidth=0, zorder=0)
    ax.text(0.485, 0.015, "below the diagonal, encoding helps", ha="right", va="bottom",
            fontsize=9, color=INK_MUTED)
    ax.plot(p, p, color=INK_SECONDARY, lw=1.6, label="Unencoded qubit: logical = physical")
    lines = (("rep3_bit_flip", "rep3", "-", "3-qubit code, bit-flip noise"),
             ("rep3_depolarizing", "rep3", ":",
              "3-qubit code, depolarizing noise (never breaks even)"),
             ("shor", "shor", "-", "Shor 9-qubit code (17 qubits), depolarizing"),
             ("surface3", "surface3", "-", "Surface code d=3 (17 qubits), depolarizing"))
    for key, color_key, style, label in lines:
        color = SERIES_COLOR[color_key]
        ax.plot(p, [curves[key](x) for x in p], style, color=color, lw=2, label=label)
        if key in sweeps:
            s = sweeps[key]
            ax.errorbar(s.p, s.rate, yerr=confidence_halfwidth(s.rate, s.shots), fmt="o", ms=5.5,
                        color=color, mec=CHART_SURFACE, mew=1.2, elinewidth=1, zorder=4)
    for key in (k for k in ("rep3_bit_flip", "shor", "surface3") if break_even[k] is not None):
        ax.plot([break_even[key]], [break_even[key]], "o", ms=10, mfc="none", mec=INK, mew=1.3,
                zorder=5, clip_on=False)
    if break_even["surface3"] is not None:
        ax.annotate(f"Shor-9 breaks even at {percent(break_even['shor'], 1)}\n"
                    f"surface d=3 at {percent(break_even['surface3'], 1)}\n(code-capacity noise)",
                    xy=(break_even["surface3"], break_even["surface3"]), xytext=(0.02, 0.40),
                    fontsize=9, color=INK,
                    arrowprops={"arrowstyle": "-", "color": INK_MUTED, "lw": 0.8})
    ax.annotate("3-qubit code (bit flips only)\nbreaks even at exactly 50%\n(code-capacity noise)",
                xy=(0.5, 0.5), xytext=(0.29, 0.10), fontsize=9, color=INK,
                arrowprops={"arrowstyle": "-", "color": INK_MUTED, "lw": 0.8})
    ax.set_xlim(0, 0.5)
    ax.set_ylim(0, 0.75)


def draw_threshold_panel(ax, exact3: Callable[[float], float], d5_rates: Sequence[float],
                         threshold: float | None) -> None:
    ps = np.asarray(P_SWEEP_LOG)
    dense = np.geomspace(ps[0], ps[-1], 200)
    ax.loglog(dense, dense, color=INK_SECONDARY, lw=1.6)
    ax.loglog(dense, [exact3(x) for x in dense], color=SERIES_COLOR["surface3"], lw=2)
    rates = np.asarray(d5_rates)
    ax.errorbar(ps, rates, yerr=confidence_halfwidth(rates, SURFACE_D5_SAMPLES), fmt="o-",
                color=SERIES_COLOR["surface5"], lw=2, ms=5.5, mec=CHART_SURFACE, mew=1.2,
                elinewidth=1, label="Surface code d=5 (49 qubits), depolarizing, sampled")
    ax.text(ps[0] * 1.08, exact3(ps[0]) * 1.6, r"d=3: $\propto p^2$", fontsize=9,
            color=INK_SECONDARY)
    ax.text(ps[1], rates[0] * 0.45, r"d=5: $\propto p^3$", fontsize=9, color=INK_SECONDARY)
    if threshold is not None:
        ax.axvline(threshold, color=INK_MUTED, lw=1)
        label = f"threshold ~{threshold:.1%}\n(code-capacity noise;\n~1% on real hardware)"
        ax.text(threshold * 0.92, 2.5e-4, label, ha="right",
                fontsize=9, color=INK)
        ax.text(0.0105, 0.3, "below threshold:\nthe bigger code wins", fontsize=9, color=INK_MUTED)
        ax.text(threshold * 1.08, 2.5e-4, "above:\nbigger\nis worse", fontsize=9, color=INK_MUTED)
    ax.set_xlim(ps[0] * 0.95, ps[-1] * 1.05)
    positive = rates[rates > 0]
    ax.set_ylim(positive.min() * 0.3 if positive.size else 1e-6, 1.0)


def plot_threshold_chart(curves: dict[str, Callable[[float], float]], sweeps: dict[str, Sweep],
                         d5_rates: Sequence[float], analysis: ThresholdAnalysis,
                         path: Path) -> None:
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(14, 7.2), dpi=160)
    fig.patch.set_facecolor(CHART_SURFACE)
    style_axes(ax_a, "A.  Break-even: does one encoded qubit beat a bare one?")
    style_axes(ax_b, "B.  Threshold: does a bigger code help?  (log-log)")
    draw_break_even_panel(ax_a, curves, sweeps, analysis.break_even)
    draw_threshold_panel(ax_b, curves["surface3"], d5_rates, analysis.threshold)
    handles, labels = {}, []
    for ax in (ax_a, ax_b):
        for handle, label in zip(*ax.get_legend_handles_labels()):
            if label not in handles:
                handles[label] = handle
                labels.append(label)
    fig.legend([handles[label] for label in labels], labels, loc="lower center", ncol=3,
               frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
    fig.suptitle("Quantum error correction: logical vs physical error rate", x=0.055, y=0.975,
                 ha="left", fontsize=15, color=INK)
    fig.text(0.055, 0.925, CHART_SUBTITLE, fontsize=9, color=INK_SECONDARY, va="top",
             linespacing=1.5)
    fig.subplots_adjust(left=0.055, right=0.985, top=0.79, bottom=0.17, wspace=0.2)
    fig.savefig(path, dpi=160, facecolor=CHART_SURFACE)
    plt.close(fig)


def surface_code_distance_needed(p: float, target: float, p_threshold: float = 0.01,
                                 prefactor: float = 0.1) -> int:
    """Smallest odd d with prefactor * (p / p_th)^((d+1)/2) <= target (a planning rule of thumb)."""
    if not 0 < p < p_threshold:
        raise ValueError("below-threshold operation requires 0 < p < p_threshold")
    d = 3
    while prefactor * (p / p_threshold) ** ((d + 1) / 2) > target * (1 + 1e-9):   # float slack
        d += 2
    return d


def print_overhead_table(target: float = 1e-12) -> None:
    print(f"\nOVERHEAD: surface-code qubits needed for a logical error rate of {target:.0e}")
    print("(rule of thumb P_L = 0.1 * (p / 1%)^((d+1)/2); circuit-level threshold taken as 1%)\n")
    print(f"  {'physical p':>10}   {'gain per +2 distance':>20}   {'distance':>8}   "
          f"{'physical per logical':>20}   {'for 100 logical':>15}")
    for p in (0.005, 0.002, 0.001, 0.0001):
        d = surface_code_distance_needed(p, target)
        per_logical = 2 * d * d - 1
        print(f"  {p:>10.2%}   {0.01 / p:>19.0f}x   {d:>8}   {per_logical:>20,}   "
              f"{100 * per_logical:>15,}")
    print("\n  For scale: Willow measured about 2.1x per +2 distance (Nature, 2024). IBM's gross")
    print("  qLDPC code protects 12 logical qubits with 288 physical qubits, where Bravyi et al.")
    print("  estimate a surface code needs nearly 3,000 at p = 0.1% (long-range couplers are")
    print("  the price). Better physical qubits shrink every row of this table.")


def part6_threshold_chart(curves: dict[str, Callable[[float], float]], sweeps: dict[str, Sweep],
                          d5_rates: tuple[float, ...]) -> tuple[ThresholdAnalysis, list[Check]]:
    section("PART 6 -- THE THRESHOLD CHART")
    break_even = {key: break_even_point(curve) for key, curve in curves.items()}
    d3_on_grid = [curves["surface3"](float(p)) for p in P_SWEEP_LOG]
    analysis = ThresholdAnalysis(MappingProxyType(break_even),
                                 interpolate_crossing(P_SWEEP_LOG, d5_rates, P_SWEEP_LOG),
                                 interpolate_crossing(P_SWEEP_LOG, d5_rates, d3_on_grid))
    plot_threshold_chart(curves, sweeps, d5_rates, analysis, OUTPUT_PNG)
    print(f"Chart written to {OUTPUT_PNG}\n")
    names = {"rep3_bit_flip": "3-qubit code, bit-flip noise", "rep3_depolarizing":
             "3-qubit code, depolarizing noise", "shor": "Shor 9-qubit, depolarizing",
             "surface3": "Surface d=3, depolarizing"}
    print("Break-even points (pseudo-thresholds), code-capacity noise:")
    for key, name in names.items():
        print(f"  {name:<36} {percent(break_even[key]):>8}")
    print(f"  {'Surface d=5, depolarizing (sampled)':<36} {percent(analysis.d5_break_even, 1):>8}")
    gain = f"{d3_on_grid[0] / d5_rates[0]:.0f}x" if d5_rates[0] > 0 else "immeasurably"
    print("\nThreshold estimate under code-capacity noise, where the d=3 and d=5 curves cross: "
          f"{percent(analysis.threshold, 1, 'not found')}")
    print("  Under circuit-level noise (noisy checks, as on real hardware) it is about 1%.")
    print(f"  At p = {P_SWEEP_LOG[0]:.0%}: d=3 fails {d3_on_grid[0]:.2e}, d=5 fails "
          f"{d5_rates[0]:.2e}: {gain} better for 32 more qubits.")
    print("  A two-size crossing is a finite-size estimate. For comparison, the asymptotic")
    print("  code-capacity threshold of matching decoders under this noise is about 15%")
    print("  (10.3% for bit flips alone, Wang, Harrington & Preskill 2003, scaled by 3/2).")
    slope = np.log(curves["surface3"](2e-3) / curves["surface3"](1e-3)) / np.log(2)
    checks = [
        Check("Part 6: chart file written",
              OUTPUT_PNG.exists() and OUTPUT_PNG.stat().st_size > 20_000,
              f"{OUTPUT_PNG.stat().st_size:,} bytes" if OUTPUT_PNG.exists() else "missing"),
        Check("Part 6: d=3 logical error scales as p^2 at low p (distance 3)",
              abs(slope - 2) < 0.05,
              f"log-log slope {slope:.3f}"),
        Check("Part 6: below threshold d=5 beats d=3; above it d=5 is worse",
              d5_rates[0] < d3_on_grid[0] and d5_rates[-1] > d3_on_grid[-1]
              and analysis.threshold is not None,
              f"crossing at {percent(analysis.threshold, 2, 'none')}"),
    ]
    print_overhead_table()
    return analysis, checks
