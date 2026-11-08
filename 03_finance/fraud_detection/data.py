"""Load the transactions, split them, draw the fair subsample, and shrink it to qubit size.

Everything that decides WHICH rows a model may see lives here, so leakage can be checked
in one place: validation and test rows are never in the subsample, and the feature
reduction is fitted on the subsample's rows only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.io import arff
from sklearn.decomposition import PCA
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from config import (EXPECTED_FEATURES, EXPECTED_FRAUDS, EXPECTED_ROWS, N_QUBITS, SEED,
                    SUBSAMPLE_PER_CLASS, TEST_FRACTION, VAL_FRACTION, Check, check)


@dataclass(frozen=True)
class Dataset:
    X: np.ndarray                 # Time, V1..V28 (anonymised PCA components), log(1 + Amount)
    y: np.ndarray                 # 1 = fraud
    amount: np.ndarray            # raw Amount, kept for reporting
    feature_names: tuple[str, ...]


@dataclass(frozen=True)
class Splits:
    train: np.ndarray
    val: np.ndarray
    test: np.ndarray


def load_dataset(path: Path) -> Dataset:
    raw, meta = arff.loadarff(path)
    names = tuple(n for n in meta.names() if n != "Class")
    X = np.column_stack([raw[n].astype(float) for n in names])
    amount = X[:, names.index("Amount")].copy()
    X[:, names.index("Amount")] = np.log1p(amount)   # amounts are heavily skewed
    y = np.array([int(v) for v in raw["Class"]], dtype=np.int8)
    return Dataset(X, y, amount, names)


def dataset_checks(data: Dataset) -> list[Check]:
    rows, frauds = len(data.y), int(data.y.sum())
    return [check("Data: 284,807 transactions, 492 frauds, 30 features (publisher's counts)",
                  (rows, frauds, data.X.shape[1])
                  == (EXPECTED_ROWS, EXPECTED_FRAUDS, EXPECTED_FEATURES),
                  f"{rows:,} rows, {frauds} frauds, {data.X.shape[1]} features"),
            check("Data: no missing values", bool(np.isfinite(data.X).all()))]


def split(y: np.ndarray) -> Splits:
    """Stratified 60/20/20: every split keeps the same fraud rate.

    Random, not by time. A live monitoring system trains on the past and scores the
    future, which is harder; with two days of data a time split would leave too few
    frauds to measure anything, so this is a stated simplification.
    """
    rows = np.arange(len(y))
    train, rest = train_test_split(rows, test_size=VAL_FRACTION + TEST_FRACTION, stratify=y,
                                   random_state=SEED)
    val, test = train_test_split(rest, test_size=TEST_FRACTION / (VAL_FRACTION + TEST_FRACTION),
                                 stratify=y[rest], random_state=SEED)
    return Splits(np.sort(train), np.sort(val), np.sort(test))


def split_checks(splits: Splits, y: np.ndarray) -> list[Check]:
    parts = (splits.train, splits.val, splits.test)
    every_row_once = np.array_equal(np.sort(np.concatenate(parts)), np.arange(len(y)))
    overall = y.mean()
    rates = [y[p].mean() for p in parts]
    stratified = all(abs(r - overall) * len(p) <= 1.0 for r, p in zip(rates, parts))
    return [check("Splits: train, validation and test are disjoint and cover every row",
                  every_row_once),
            check("Splits: each keeps the overall fraud rate (within one transaction)",
                  stratified, ", ".join(f"{int(y[p].sum())} frauds" for p in parts))]


def draw_subsample(y: np.ndarray, train: np.ndarray, draw: int) -> np.ndarray:
    """A balanced subsample from TRAINING rows only: SUBSAMPLE_PER_CLASS of each class."""
    rng = np.random.default_rng([SEED, 31, draw])
    frauds, legit = train[y[train] == 1], train[y[train] == 0]
    if SUBSAMPLE_PER_CLASS > len(frauds):
        raise ValueError(f"SUBSAMPLE_PER_CLASS = {SUBSAMPLE_PER_CLASS} but the training split "
                         f"holds only {len(frauds)} frauds")
    return np.sort(np.concatenate([rng.choice(frauds, SUBSAMPLE_PER_CLASS, replace=False),
                                   rng.choice(legit, SUBSAMPLE_PER_CLASS, replace=False)]))


def draw_overlap(y: np.ndarray, train: np.ndarray, draws: int) -> float:
    """Average share of frauds two draws have in common. The training split holds only
    about 295 frauds, so 200-fraud draws overlap heavily: they are NOT independent samples,
    and their spread understates how much a truly fresh dataset would move results."""
    sets = [set(r for r in draw_subsample(y, train, d) if y[r] == 1) for d in range(draws)]
    pairs = [(a, b) for i, a in enumerate(sets) for b in sets[i + 1:]]
    return float(np.mean([len(a & b) / SUBSAMPLE_PER_CLASS for a, b in pairs])) if pairs else 0.0


def subsample_checks(y: np.ndarray, splits: Splits) -> list[Check]:
    first, again, other = (draw_subsample(y, splits.train, d) for d in (0, 0, 1))
    outside = np.setdiff1d(first, splits.train)
    return [check(f"Subsample: {SUBSAMPLE_PER_CLASS} frauds + {SUBSAMPLE_PER_CLASS} legitimate, "
                  "all from training rows",
                  int(y[first].sum()) == SUBSAMPLE_PER_CLASS
                  and len(first) == 2 * SUBSAMPLE_PER_CLASS and outside.size == 0,
                  f"{outside.size} rows from outside the training split"),
            check("Subsample: same draw number gives the same rows; a new draw gives new rows",
                  np.array_equal(first, again) and not np.array_equal(first, other))]


@dataclass(frozen=True)
class Reducer:
    """Standardise, project onto N_QUBITS principal components, rescale to unit variance.

    Fitted on the subsample's rows only, then applied unchanged to validation and test.
    The same reduced features feed every model in the fair comparison.
    """

    scaler: StandardScaler
    pca: PCA
    spread: np.ndarray

    def transform(self, X: np.ndarray) -> np.ndarray:
        return self.pca.transform(self.scaler.transform(X)) / self.spread


def reducer_leakage_check(X: np.ndarray, sub: np.ndarray) -> Check:
    """Scramble every row OUTSIDE the subsample; a reducer that only looks at the subsample
    must come out identical. One that peeked at other rows (training, validation or test)
    would change."""
    rng = np.random.default_rng([SEED, 19])
    scrambled = rng.normal(size=X.shape) * 10.0
    scrambled[sub] = X[sub]
    a, b = fit_reducer(X, sub), fit_reducer(scrambled, sub)
    probe = X[sub[:50]]
    same = np.allclose(a.transform(probe), b.transform(probe), atol=1e-10)
    return check("Features: reduction ignores every row outside the subsample (other rows "
                 "scrambled, fit unchanged)", bool(same))


def fit_reducer(X: np.ndarray, rows: np.ndarray) -> Reducer:
    scaler = StandardScaler().fit(X[rows])
    pca = PCA(N_QUBITS, random_state=SEED).fit(scaler.transform(X[rows]))
    spread = pca.transform(scaler.transform(X[rows])).std(axis=0)
    return Reducer(scaler, pca, spread)
