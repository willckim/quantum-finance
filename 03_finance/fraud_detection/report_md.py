"""fraud_report.md: the results in plain language, for audit, compliance and finance readers."""

from __future__ import annotations

from pathlib import Path

from config import (AER_PAIRS, AER_SHOTS, DATA_CITATION, DATA_NOTICE, FPR_TARGET, SHOT_REPEATS,
                    N_SUBSAMPLE_DRAWS, N_QUBITS, SUBSAMPLE_PER_CLASS, VERSION)
from report import Results, markdown_table, summary_rows, verdict

FCA_SOURCE = ("https://www.fca.org.uk/news/press-releases/"
              "fca-explore-quantum-computing-tackle-financial-crime")


def comparison_table(r: Results) -> str:
    rows = ["| Comparison (test PR-AUC) | Difference | 95% bootstrap interval | Reading |",
            "|---|---|---|---|"]
    for c in r.comparisons:
        reading = ("real difference" if c.significant
                   else "not distinguishable from zero")
        rows.append(f"| {c.label} | {c.difference:+.3f} | {c.low:+.3f} to {c.high:+.3f} | "
                    f"{reading} |")
    return "\n".join(rows)


def stability_table(r: Results) -> str:
    rows = ["| Model (fair comparison) | Mean PR-AUC | Lowest | Highest |", "|---|---|---|---|"]
    for name, values in r.stability.items():
        rows.append(f"| {name} | {sum(values) / len(values):.3f} | {min(values):.3f} | "
                    f"{max(values):.3f} |")
    return "\n".join(rows)


def shots_table(r: Results) -> str:
    exact = next(o for o in r.ops if o.model.name == "Quantum kernel SVM")
    rows = [f"| Shots per kernel entry | Typical shot error per entry | Test PR-AUC, mean of "
            f"{SHOT_REPEATS} noise draws | Range |",
            "|---|---|---|---|", f"| exact (simulator) | 0 | {exact.pr_auc:.3f} | |"]
    rows += [f"| {s.shots:,} | {s.shot_std_err:.4f} | {s.pr_auc_mean:.3f} | "
             f"{s.pr_auc_low:.3f} to {s.pr_auc_high:.3f} |" for s in r.shots]
    return "\n".join(rows)


def sensitivity_sentence(r: Results) -> str:
    winners = {label for _, label, _ in r.sensitivity}
    ratios = [ratio for ratio, _, _ in r.sensitivity]
    if len(winners) == 1:
        return (f"The same model, {winners.pop()}, is cheapest at every ratio from "
                f"{ratios[0]} to {ratios[-1]}: the ranking here does not hinge on the cost "
                "assumption.")
    return (f"The cheapest model changes across ratios {ratios[0]} to {ratios[-1]} "
            f"({', '.join(sorted(winners))}): the choice of model depends on the cost "
            "assumption, so that assumption needs evidence.")


def by_name(r: Results, name: str, regime: str):
    return next(o for o in r.ops if o.model.name == name and o.model.regime == regime)


def why_lines(r: Results) -> list[str]:
    """The quantum explanation, with every comparative word chosen by the numbers."""
    quantum = by_name(r, "Quantum kernel SVM", "subsample")
    rbf = by_name(r, "RBF SVM", "subsample")
    full_best = max((o for o in r.ops if o.model.regime == "full"), key=lambda o: o.pr_auc)
    fair_best = max((o for o in r.ops if o.model.regime == "subsample"), key=lambda o: o.pr_auc)
    params = quantum.model.params
    q_shown, rbf_shown = round(quantum.pr_auc, 3), round(rbf.pr_auc, 3)
    relation = ("level with" if q_shown == rbf_shown
                else "above" if q_shown > rbf_shown else "below")
    noisy = r.shots[0]
    hurt = noisy.pr_auc_high < quantum.pr_auc
    total = (r.circuits["train"] + r.circuits["score"]) * noisy.shots
    return [
        f"* **It had to work with less.** Quantum kernels need one circuit per pair of "
        f"transactions, so the model trained on {2 * SUBSAMPLE_PER_CLASS} rows reduced to "
        f"{N_QUBITS} features (one per qubit). With all {r.train_size:,} training rows and 30 "
        f"features, {full_best.model.name.lower()} reaches PR-AUC {full_best.pr_auc:.3f}; the "
        f"best model on the small subsample reaches {fair_best.pr_auc:.3f}.",
        f"* **Its best setting was its most classical one.** Validation tuning chose bandwidth "
        f"{params['bandwidth']} with {params['reps']} repetition(s). There the average overlap "
        f"between two different transactions is {r.kernel_mean:.3f} (1 = identical), with "
        f"entries differing by only about {r.kernel_spread:.4f}. In that regime the quantum "
        "kernel behaves like a smooth classical kernel (Shaydulin & Wild, 2022), so it competes "
        f"with the RBF SVM rather than doing something new: it scored {q_shown:.3f}, "
        f"{relation} the RBF SVM's {rbf_shown:.3f}.",
        "* **Both kernels wanted to be as simple as possible.** Validation pushed the quantum "
        "kernel and the RBF SVM toward the smooth end of their grids (smallest bandwidth or "
        "gamma, weakest regularisation), and both were still improving at the edge. That is "
        "the direction in which each kernel becomes SIMPLER: an RBF SVM approaches a linear "
        "SVM as gamma shrinks and C grows (Keerthi & Lin, 2003), and a quantum kernel at small "
        "bandwidth depends, to leading order, only on squared differences between features "
        "(Shaydulin & Wild, 2022). This run did not search further, so it does not show where "
        "the limit lies; it does show the data rewarding simpler decision boundaries on these "
        "6 features, which leaves little room for a richer quantum feature space to help.",
        f"* **Shot noise {'makes it worse' if hurt else 'did not clearly hurt it here'}.** "
        "Each kernel entry on a real device is an estimate from repeated measurements (shots). "
        "The useful differences between entries are tiny, so they compete with shot noise. "
        f"Each level below was run {SHOT_REPEATS} times with fresh noise, re-tuning C on a "
        "noisy validation kernel each time; the bandwidth stays at its noiseless choice, so a "
        "model tuned entirely on noisy hardware could do somewhat better. Gate errors and "
        "drift, which real devices add on top, are not modelled:",
        "",
        shots_table(r),
        "",
        f"Training needs {r.circuits['train']:,} circuits and scoring this test set needs "
        f"{r.circuits['score']:,} more (one per support vector per transaction). At "
        f"{noisy.shots:,} shots each that is {total:,.0f} circuit executions for one "
        "evaluation, and every new transaction in production would need its own "
        "one-circuit-per-support-vector scoring step.",
        f"* The quantum kernel code was checked against qiskit's own circuit simulation, and "
        f"{AER_PAIRS} overlaps were measured the hardware way ({AER_SHOTS:,} shots each) on the "
        "Aer simulator and matched. The result reflects the method, not a bug.",
    ]


def write_report(r: Results, path: Path) -> None:
    lines = [
        "# Fraud detection: classical models vs a quantum kernel",
        "",
        f"Generated by `run_all.py` v{VERSION} on {r.run_time:%Y-%m-%d %H:%M}. Every number "
        "below comes from that run.",
        "",
        "## Bottom line",
        "",
        *[f"* {line}" for line in verdict(r)],
        "",
        "This is a simulation of a quantum computer with no noise at all, which is the most "
        "favourable setting a quantum model can get. Real hardware would add measurement "
        "noise (see the shot-noise table) and gate errors (not modelled).",
        "",
        "## Results on the held-out test set",
        "",
        f"Test set: {r.test_size:,} transactions, {r.test_frauds} of them fraud "
        f"({r.prevalence:.2%}). No model saw these rows during training or tuning. Costs use "
        f"{r.miss_cost:,.0f} per missed fraud and {r.review_cost:,.0f} per analyst review "
        "(illustrative figures; change them with `--miss-cost` and `--review-cost`). Each "
        "model's alert threshold was set on the validation set to minimise cost, then "
        "applied unchanged here.",
        "",
        markdown_table(summary_rows(r)),
        "",
        "| Baseline | Test cost |",
        "|---|---|",
        *[f"| {name} | {cost:,.0f} |" for name, cost in r.baselines.items()],
        "",
        "Accuracy is shown last on purpose. Approving every transaction would score "
        f"{1 - r.prevalence:.2%} accuracy while catching no fraud at all, so a high accuracy "
        "says almost nothing here. PR-AUC (area under the precision-recall curve) and recall "
        f"at a {FPR_TARGET:.1%} false-positive rate measure what an investigations team "
        "actually cares about: how much fraud is caught for a given volume of alerts.",
        "",
        "![Precision-recall curves](fraud_pr_curves.png)",
        "",
        "## Is the difference real?",
        "",
        "With only about 100 frauds in the test set, small PR-AUC gaps can be luck. A paired "
        "bootstrap (the test set resampled 1,000 times, the same rows for both models) gives "
        "a 95% interval for each difference. The classical comparator in the first row is "
        "the subsample model with the best VALIDATION score, so the test set did not choose it.",
        "",
        comparison_table(r),
        "",
        f"The fair comparison was repeated on {N_SUBSAMPLE_DRAWS} subsample draws, each tuned "
        "afresh. The draws are NOT independent: the training split holds only "
        f"{r.train_frauds} frauds and each draw takes {SUBSAMPLE_PER_CLASS}, so any two "
        f"share about {r.draw_overlap:.0%} of their frauds, and all use the same validation "
        "and test sets. The spread below therefore understates how much results would move "
        "on genuinely new data:",
        "",
        stability_table(r),
        "",
        "## Does the winner change with the cost ratio?",
        "",
        "The review cost is held fixed and the cost of a missed fraud is varied. Each model's "
        "threshold is re-chosen on validation for every ratio.",
        "",
        "| Missed fraud costs N x a review | Cheapest model | Test cost |",
        "|---|---|---|",
        *[f"| {ratio} | {label} | {cost:,.0f} |" for ratio, label, cost in r.sensitivity],
        "",
        sensitivity_sentence(r),
        "",
        "## Why the quantum kernel did what it did",
        "",
        *why_lines(r),
        "",
        "## What this means for fraud and AML monitoring",
        "",
        "* **A fraud model is an alert queue.** Raising the threshold means fewer alerts and "
        "more missed fraud; lowering it means more analyst hours. Recall at a fixed "
        "false-positive rate asks the operational question directly: with the alert volume "
        "the team can handle, how much fraud is caught? The cost table makes the same trade "
        "in money. The right threshold, and potentially the right model, depends on what a "
        "missed fraud really costs the organisation, so that figure deserves as much scrutiny "
        "as the model.",
        "* **For auditors and model risk reviewers**, this project shows the evidence to ask "
        "for before accepting any detection model, quantum or not: the same data for every "
        "model, equal tuning budgets for the models compared head to head, a test set held "
        "back until the end, thresholds set before the test, uncertainty on every comparison, "
        "and a run log that reproduces the numbers. Bank model risk guidance, such as the "
        "PRA's SS1/23 (UK) and the Federal Reserve's SR 11-7 (US), expects independent "
        "validation, benchmarking against alternative models and documentation; evidence of "
        "this kind is how a firm would meet it. A quantum model would be a challenger model "
        "and should clear the same bar.",
        "* **Card fraud is the easier problem.** Each transaction here is judged on its own and "
        "labels arrive quickly (cardholders dispute charges). Money laundering is spread across "
        "accounts and time (placement, layering, integration), so AML monitoring looks at "
        "networks of payments, and confirmed labels are scarce and slow. IBM's synthetic "
        "AMLworld data models that, but at 41.6 GB it is beyond a laptop project; the same "
        "comparison method applies.",
        "",
        "## What the FCA is testing",
        "",
        "On 5 November 2026 the UK Financial Conduct Authority announced a Quantum "
        "Experimentation Group, which it describes as the first regulator-led group of its "
        "kind. Its first use case is financial crime: participants will try quantum machine "
        "learning for anti-money laundering and fraud detection on a common FCA synthetic "
        "dataset and compare it with classical techniques. The group launches in Q1 2027; "
        "firms, quantum providers and research labs can register interest until 31 December "
        "2026. The FCA notes that taking part is not an endorsement of any technology "
        f"([FCA press release]({FCA_SOURCE})).",
        "",
        "This project is a small, public version of the same experiment. It does not predict "
        "what the FCA's group will find: their data, hardware access and models will differ. "
        "It does show the questions that decide the answer: is the classical baseline as "
        "strong as it can be, is the comparison on equal data, and does the result survive "
        "real hardware noise and production volumes?",
        "",
        "## Limitations",
        "",
        "* Some tuned values sit at the end of their grid (listed below). For the two kernels "
        "this is the simplifying direction explained above; for all of them a wider grid "
        "could shift results.",
        *[f"  * {note}" for note in r.edge_notes],
        "* One public card-fraud dataset: two days of European transactions from 2013, with "
        "features already anonymised by PCA. Results may not transfer to other portfolios.",
        "* Random split, not by time. Live systems score future transactions with models "
        "trained on past ones, which is harder.",
        f"* Time (seconds since the first transaction) is a feature, and frauds cluster in "
        f"time, so under a random split a model can partly learn WHEN fraud happened. Refitting "
        f"{r.without_time[0].lower()} on full data without Time gives PR-AUC "
        f"{r.without_time[2]:.3f}, against {r.without_time[1]:.3f} with it.",
        "* About 100 test frauds, so small differences are within noise (see the intervals). "
        "The ranks and the cheapest-model choices carry no interval of their own; read gaps "
        "smaller than the bootstrap intervals as ties.",
        "* The quantum kernel ran on a noiseless simulator. The shot-noise test adds "
        "measurement noise only; real devices also have gate errors and drift.",
        "* One quantum feature map (qiskit's ZZ map) at one qubit count. Other encodings or "
        "trainable kernels could do better or worse; a fair test would give them the same "
        "budget the classical models got.",
        "* Costs are illustrative, not sourced. Replace them with your organisation's figures.",
        "",
        "## Data",
        "",
        f"{DATA_NOTICE} Citation: {DATA_CITATION} The raw file is not stored in this "
        "repository; `download_data.py` fetches it and checks its published MD5. The mean "
        f"fraudulent transaction in the data is {r.mean_fraud_amount:,.2f} euros.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
