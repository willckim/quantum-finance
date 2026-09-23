"""pqc_readiness_chart.png: the Mosca exposure timeline by data type, one panel per scenario.

HOW TO READ IT
--------------
Each bar starts at the as-of year and runs for y + x years: the migration window (light
blue, new records still encrypted the old way) followed by the shelf life of the LAST
such record (blue). The gray band is the scenario's planning window for a
cryptographically relevant quantum computer (CRQC); the black line is the planning year
the calculation uses. Anything right of the line is red: years that record would still
be sensitive after a CRQC could read a recording of it. The windows are planning
scenarios, not predictions.

Colors were checked with the dataviz palette validator: the two blues pass as a one-hue
ordinal ramp, and blue vs red passes the colorblind-separation and contrast checks. The
red is a status color (it means "at risk"), and every red bar is also labeled in text.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # save to file, never open a window
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from config import Config  # noqa: E402
from mosca import EXPOSED_NOW, SAFE, Assessment  # noqa: E402

SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
MIGRATION, SHELF, EXPOSED_RED, WINDOW = "#86b6ef", "#2a78d6", "#d03b3b", "#ebeae4"
BAR_HEIGHT = 0.62


def _bar(ax, i: int, a: Assessment, as_of: int) -> None:
    crqc, end = a.scenario.crqc_year, as_of + a.y + a.x
    kw = {"height": BAR_HEIGHT, "edgecolor": SURFACE, "linewidth": 1.5, "zorder": 2}
    ax.barh(i, a.y, left=as_of, color=MIGRATION, **kw)
    ax.barh(i, a.x, left=as_of + a.y, color=SHELF, **kw)
    if a.status != SAFE:
        ax.barh(i, end - crqc, left=crqc, color=EXPOSED_RED, **kw)
        ax.text(end + 0.5, i, f"+{a.exposure_years} yr", va="center", fontsize=9, color=INK,
                fontweight="bold" if a.status == EXPOSED_NOW else "normal")
    else:   # keep the label clear of the planning-year line when a bar ends just before it
        ax.text(max(end, crqc) + 0.5, i, "safe", va="center", fontsize=9, color=INK_2)


def _panel(ax, rows: list[Assessment], as_of: int, x_max: int) -> None:
    s = rows[0].scenario
    ax.axvspan(s.window[0], s.window[1], color=WINDOW, zorder=0)
    for i, a in enumerate(rows):
        _bar(ax, i, a, as_of)
    ax.axvline(s.crqc_year, color=INK, linewidth=1.2, zorder=3)
    exposed = sum(a.status != SAFE for a in rows)
    now = sum(a.status == EXPOSED_NOW for a in rows)
    ax.set_title(f"{s.name}: window {s.window[0]}-{s.window[1]}, calculated at "
                 f"{s.crqc_year}\n{exposed} of {len(rows)} exposed  |  {now} "
                 "exposed even if migrated today (bold)", fontsize=10, color=INK, loc="left",
                 pad=8)
    ax.set_xlim(as_of - 1, x_max)
    ax.set_ylim(len(rows) - 0.4, -0.6)
    ax.grid(axis="x", color=GRID, linewidth=0.6, zorder=1)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.set_facecolor(SURFACE)


def render_chart(cfg: Config, results: dict[str, list[Assessment]], path: Path) -> Path:
    as_of, y = cfg.as_of_year, cfg.migration_years
    first = next(iter(results.values()))
    names = [a.data.name + (" (assumption)" if a.data.assumption else "") for a in first]
    x_max = max(as_of + a.y + a.x for rows in results.values() for a in rows) + 6
    fig, axes = plt.subplots(1, len(results), figsize=(16, 6.4), sharey=True,
                             facecolor=SURFACE)
    for ax, rows in zip(axes, results.values()):
        _panel(ax, rows, as_of, x_max)
    axes[0].set_yticks(range(len(names)), names, fontsize=10, color=INK)
    fig.suptitle("Mosca exposure timeline: finance data still sensitive when a quantum "
                 "computer might arrive", x=0.01, ha="left", fontsize=14, color=INK,
                 fontweight="bold", y=0.985)
    fig.text(0.01, 0.945, f"Each bar runs from {as_of} for y + x years: a {y}-year migration "
             "window, then the shelf life of the last record encrypted before migration "
             "finishes.\nGray band = the scenario's planning window (not a prediction); "
             "black line = the planning year used; red = years still sensitive after it.",
             fontsize=9.5, color=INK_2, ha="left", va="top")
    fig.legend(handles=[Patch(color=MIGRATION, label=f"Migration window (y = {y} yr)"),
                        Patch(color=SHELF, label="Shelf life (x, from the config)"),
                        Patch(color=EXPOSED_RED, label="Exposed after the planning year"),
                        Patch(color=WINDOW, label="Scenario planning window")],
               loc="lower left", bbox_to_anchor=(0.01, 0.035), ncol=4, frameon=False,
               fontsize=10, labelcolor=INK)
    fig.text(0.01, 0.012, f"Synthetic inventory; default shelf lives from {cfg.path_label} "
             "(general US federal rules). Bank credentials use an ASSUMPTION (NIST key "
             "cryptoperiod), not a retention rule. Replace with your own retention policy.",
             fontsize=8.5, color=MUTED, ha="left")
    fig.tight_layout(rect=(0, 0.09, 1, 0.88))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path
