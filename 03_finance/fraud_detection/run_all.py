"""Fraud detection, classical vs quantum: one run, every model, every check.

WHAT THIS PROJECT DOES
  Card fraud is rare (0.17% of the transactions here), so the job is to rank 57,000
  held-out transactions so the frauds sit near the top of an analyst's alert queue. Six
  models try, in two comparisons:
    * FULL DATA: logistic regression and gradient boosting on all 170,000+ training rows.
      What a bank would actually deploy today.
    * FAIR COMPARISON: a quantum kernel SVM can only be trained on a few hundred rows (one
      circuit per PAIR of rows), so it gets a balanced 400-row subsample reduced to 6
      features, one per qubit. Logistic regression, gradient boosting and a classical RBF
      SVM get exactly the same rows and features. The two kernel SVMs, the head-to-head
      pair, get the same tuning budget (36 settings each); the others get 7 and 8.
  It was inspired by the UK FCA's Quantum Experimentation Group (announced 5 November
  2026), whose first use case is quantum machine learning for fraud and anti-money
  laundering detection, compared against classical techniques. fraud_report.md explains
  the results in plain language for audit and compliance readers.

READ THE MODULES IN THIS ORDER
    config.py           every setting, including the two costs
    download_data.py    fetch the public dataset (run once; the raw file is never committed)
    data.py             splits, the fair subsample, feature reduction, leakage checks
    metrics.py          why not accuracy; PR-AUC, recall at fixed FPR, costs; answer key
    quantum_kernel.py   the feature-map circuit, the kernel, and its hardware cost
    models.py           the six models and their tuning
    analysis.py         thresholds, costs, bootstrap intervals, stability, shot noise
    chart.py, report.py, report_md.py   outputs

Run:  py download_data.py   (once, 150 MB)
      py run_all.py [--miss-cost 300] [--review-cost 20]      (about 3 minutes)
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from datetime import datetime
from typing import Sequence

import numpy as np
import qiskit
import sklearn

from analysis import (baseline_costs, compare, cost_sensitivity, kernel_spread, operate,
                      scorer_check, shot_noise_test, stability)
from chart import plot_pr_curves
from config import (DATA_MD5, DATA_PATH, MISS_COST, N_QUBITS, N_SUBSAMPLE_DRAWS, PR_CHART,
                    QK_BANDWIDTH, RBF_GAMMA, REPORT_MD, REVIEW_COST, RUN_LOG, SEED,
                    SUMMARY_CSV, Check, check, section, validate_settings)
from data import (dataset_checks, draw_overlap, draw_subsample, fit_reducer, load_dataset,
                  reducer_leakage_check, split, split_checks, subsample_checks)
from download_data import md5_of
from metrics import accuracy_paradox_check, metric_answer_key_checks, pr_auc
from models import (FairData, Scored, edge_hits, fair_models, full_boosting,
                    full_boosting_without, full_logistic)
from quantum_kernel import (aer_kernel_check, feature_map_checks, hardware_circuit_count,
                            kernel_checks)
from report import Results, print_summary, verdict, write_csv, write_run_log
from report_md import write_report

DATA_DEPENDENT = ("Data", "Splits", "Subsample", "Features", "Quantum kernel on real data",
                  "Models", "Results", "Outputs")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--miss-cost", type=float, default=MISS_COST,
                        help="cost of one missed fraud (default %(default)s)")
    parser.add_argument("--review-cost", type=float, default=REVIEW_COST,
                        help="cost of one analyst review of an alert (default %(default)s)")
    args = parser.parse_args(argv)
    if args.miss_cost <= 0 or args.review_cost <= 0:
        parser.error("costs must be positive")
    return args


def run_fair_draws(X, y, splits) -> tuple[list[list[Scored]], FairData, list[Check]]:
    draws, checks, first = [], [], None
    for d in range(N_SUBSAMPLE_DRAWS):
        sub = draw_subsample(y, splits.train, d)
        reducer = fit_reducer(X, sub)
        fair = FairData(reducer.transform(X[sub]), y[sub], reducer.transform(X[splits.val]),
                        reducer.transform(X[splits.test]))
        models = fair_models(fair, y[splits.val])
        if d == 0:
            first = fair
            checks.append(reducer_leakage_check(X, sub))
        else:                       # keep the large kernel arrays for the primary draw only
            models = [dataclasses.replace(m, extra={}) for m in models]
        draws.append(models)
        print(f"  draw {d}: " + ", ".join(f"{m.name} {pr_auc(y[splits.test], m.test):.3f}"
                                         for m in models))
    return draws, first, checks


def claim_checks(ops, quantum: Scored, rbf: Scored, prevalence: float, y_test) -> list[Check]:
    """Claims the report makes in words, tested on the numbers."""
    best_cost = min(ops, key=lambda o: o.cost)
    narrow = sorted(QK_BANDWIDTH)[: len(QK_BANDWIDTH) // 2]
    smooth_gamma = sorted(RBF_GAMMA)[: len(RBF_GAMMA) // 2]
    return [
        check("Results: every model beats random ranking (PR-AUC above the fraud rate)",
              all(o.pr_auc > prevalence for o in ops),
              f"lowest {min(o.pr_auc for o in ops):.3f} vs fraud rate {prevalence:.4f}"),
        accuracy_paradox_check(y_test, best_cost.test, best_cost.label),
        check("Results: both kernel SVMs chose the smooth half of their bandwidth grids "
              "(the report's 'most classical setting' claim)",
              quantum.params["bandwidth"] in narrow and rbf.params["gamma"] in smooth_gamma,
              f"quantum bandwidth {quantum.params['bandwidth']}, RBF gamma "
              f"{rbf.params['gamma']:.0e}"),
    ]


def print_verification(checks: Sequence[Check]) -> bool:
    section("SELF-VERIFICATION")
    for c in checks:
        detail = f"  ({c.detail})" if c.detail else ""
        print(f"  [{c.status}] {c.name}{detail}")
    passed = sum(c.status == "PASS" for c in checks)
    not_run = sum(c.status == "NOT RUN" for c in checks)
    print(f"\n  {passed}/{len(checks)} checks passed"
          + (f", {not_run} NOT RUN (no data: run py download_data.py)" if not_run else "")
          + ".")
    return passed == len(checks)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    validate_settings()
    run_time = datetime.now()
    print("=" * 88)
    print("FRAUD DETECTION -- classical models vs a quantum kernel")
    print(f"Run {run_time.isoformat(timespec='seconds')} | qiskit {qiskit.__version__} | "
          f"scikit-learn {sklearn.__version__} | numpy {np.__version__} | seed {SEED}")
    print(f"Costs: {args.miss_cost:,.0f} per missed fraud, {args.review_cost:,.0f} per review")
    print("=" * 88)
    checks = metric_answer_key_checks() + feature_map_checks(N_QUBITS)

    if not DATA_PATH.exists():
        print(f"\nData file not found: {DATA_PATH}\nRun: py download_data.py")
        checks += [Check(f"{area}: checks on the dataset", "NOT RUN", "no data file")
                   for area in DATA_DEPENDENT]
        print_verification(checks)
        return 2
    actual_md5 = md5_of(DATA_PATH)
    checks.append(check("Data: file MD5 matches OpenML's published checksum",
                        actual_md5 == DATA_MD5, actual_md5))

    section("DATA")
    data = load_dataset(DATA_PATH)
    X, y = data.X, data.y
    splits = split(y)
    checks += dataset_checks(data) + split_checks(splits, y) + subsample_checks(y, splits)
    y_val, y_test = y[splits.val], y[splits.test]
    print(f"  {len(y):,} transactions, {int(y.sum())} frauds ({y.mean():.3%}). Train "
          f"{len(splits.train):,} / validation {len(splits.val):,} / test {len(splits.test):,}")

    section("FULL-DATA CLASSICAL MODELS")
    rows = (splits.train, splits.val, splits.test)
    full = [full_logistic(X, y, *rows), full_boosting(X, y, *rows)]
    for m in full:
        print(f"  {m.name:<20} test PR-AUC {pr_auc(y_test, m.test):.3f}  params {m.params}")

    section(f"FAIR COMPARISON: {N_SUBSAMPLE_DRAWS} subsample draws, test PR-AUC")
    draws, fair, more = run_fair_draws(X, y, splits)
    checks += more
    primary = draws[0]
    quantum = next(m for m in primary if m.name == "Quantum kernel SVM")
    for m in primary:
        print(f"  primary draw: {m.name:<20} params {m.params} ({m.configs_tried} configs)")
    reps, bandwidth = quantum.params["reps"], quantum.params["bandwidth"]
    checks += kernel_checks(quantum.extra["K_train"])
    checks += [aer_kernel_check(bandwidth * fair.F_sub, reps), scorer_check(quantum)]

    section("OPERATING POINTS, COSTS AND UNCERTAINTY (test set)")
    ops = [operate(m, y_val, y_test, args.miss_cost, args.review_cost) for m in full + primary]
    classical_fair = max((m for m in primary if m is not quantum),
                         key=lambda m: pr_auc(y_val, m.val))
    best_full = max(full, key=lambda m: pr_auc(y_val, m.val))
    comparisons = [
        compare(y_test, quantum, classical_fair,
                f"Quantum kernel SVM vs {classical_fair.name} (both on the subsample)"),
        compare(y_test, best_full, quantum,
                f"{best_full.name} on full data vs quantum kernel SVM"),
        compare(y_test, best_full, classical_fair,
                f"{best_full.name} on full data vs {classical_fair.name} on the subsample "
                "(more rows AND all 30 features, not separable here)"),
    ]
    gb_full = next(m for m in full if m.name == "Gradient boosting")
    keep = [i for i, name in enumerate(data.feature_names) if name != "Time"]
    no_time = pr_auc(y_test, full_boosting_without(X, y, splits.train, splits.test,
                                                   gb_full.params, keep))
    print(f"  Gradient boosting (full) without the Time feature: test PR-AUC {no_time:.3f}")
    K = quantum.extra["K_train"]
    off_diagonal = K[~np.eye(len(K), dtype=bool)]
    n_support = len(quantum.extra["model"].support_)
    results = Results(
        run_time=run_time, miss_cost=args.miss_cost, review_cost=args.review_cost, ops=ops,
        baselines=baseline_costs(y_test, args.miss_cost, args.review_cost),
        sensitivity=cost_sensitivity(full + primary, y_val, y_test, args.review_cost),
        comparisons=comparisons, edge_notes=[h for m in full + primary for h in edge_hits(m)],
        draw_overlap=draw_overlap(y, splits.train, N_SUBSAMPLE_DRAWS),
        train_frauds=int(y[splits.train].sum()),
        without_time=(gb_full.name, pr_auc(y_test, gb_full.test), no_time),
        stability=stability(draws, y_test),
        shots=shot_noise_test(quantum, fair.y_sub, y_val, y_test), kernel_spread=kernel_spread(K),
        kernel_mean=float(off_diagonal.mean()), train_size=len(splits.train),
        circuits=hardware_circuit_count(len(K), n_support, len(y_test)),
        test_size=len(y_test), test_frauds=int(y_test.sum()), prevalence=float(y_test.mean()),
        mean_fraud_amount=float(data.amount[y == 1].mean()))
    print_summary(results)
    for note in results.edge_notes:
        print(f"  tuning edge: {note}")
    print()
    for line in verdict(results):
        print(f"  * {line}")
    rbf = next(m for m in primary if m.name == "RBF SVM")
    checks += claim_checks(ops, quantum, rbf, results.prevalence, y_test)

    section("OUTPUTS")
    plot_pr_curves(y_test, full, primary, PR_CHART)
    write_report(results, REPORT_MD)
    write_csv(results, SUMMARY_CSV)
    outputs = [PR_CHART, REPORT_MD, SUMMARY_CSV, RUN_LOG]
    checks.append(check("Outputs: chart, report and summary written",
                        all(p.exists() and p.stat().st_size > 500 for p in outputs[:3]),
                        ", ".join(p.name for p in outputs[:3])))
    source = {"file": DATA_PATH.name, "md5": actual_md5, "records_read": len(y),
              "frauds": int(y.sum()), "url_note": "OpenML dataset 1597"}
    write_run_log(results, checks, outputs, source, RUN_LOG)
    for p in outputs:
        print(f"  wrote {p.name}")
    return 0 if print_verification(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
