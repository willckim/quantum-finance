"""Classical Pauli-frame reference model: the exact arithmetic the circuits are graded against.

Also home to the minimum-weight lookup decoder and the fast sampler that reaches
distance 5. Part of the error-correction walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Callable, Iterable, Sequence

import numpy as np
from scipy.optimize import brentq

from noise_and_tools import Check, bias_split


# ======================================================================================
# SECTION 0b -- CLASSICAL PAULI-FRAME REFERENCE MODEL
# ======================================================================================
# The quantum circuits in Parts 2-5 are the demonstration. This section is the independent
# arithmetic they are checked against, and the fast path that scales to distance 5.
#
# Why a classical model is exact here: every code in this script is a CSS code, and every
# error is a Pauli. A Pauli error E is fully described by two bit vectors, ex (where it has
# an X part) and ez (where it has a Z part; Y = both). Then:
#   * Z-type checks see X parts: syndrome_z = H_z . ex (mod 2). X-type checks see Z parts.
#   * The decoder maps each syndrome to a correction C. The residual is E.C.
#   * The run fails iff the residual flips a logical operator, i.e. anticommutes with
#     logical X or logical Z. (It always has zero syndrome, so it is either harmless or
#     a logical error; there is nothing in between.)
#
# SCOPE OF THE CROSS-CHECK: the circuits and this model share the code DEFINITIONS (which
# parities are checked, which operators are logical). They do NOT share the mechanics: the
# circuits discover syndromes by simulating CNOTs and measurements. In Parts 2-4 the
# circuits also decode with hand-written tables while this model uses a breadth-first-search
# decoder, so agreement grades those tables too. In Part 5 both sides use the SAME BFS
# table, so agreement grades syndrome extraction, in-circuit correction and readout, but not
# the decoder's choices; the 27 single-error cases and the distance checks cover those.
# Nothing here can catch a wrong code definition; the code-validity checks (commutation,
# one logical qubit, distance) and the textbook closed forms for the 3-qubit codes carry
# that half.

@dataclass(frozen=True)
class CssCode:
    """A CSS stabilizer code as check matrices plus its logical operators.

    x_checks rows are X-type stabilizers (they DETECT Z errors); z_checks rows are Z-type
    stabilizers (they detect X errors). Logical operators are (x_part, z_part) vectors.
    """

    name: str
    x_checks: np.ndarray
    z_checks: np.ndarray
    logical_x: tuple[np.ndarray, np.ndarray]
    logical_z: tuple[np.ndarray, np.ndarray]

    @property
    def n(self) -> int:
        return self.z_checks.shape[1]


def _support_matrix(n: int, supports: Iterable[Iterable[int]]) -> np.ndarray:
    rows = [list(s) for s in supports]
    matrix = np.zeros((len(rows), n), dtype=np.uint8)
    for i, row in enumerate(rows):
        matrix[i, row] = 1
    matrix.setflags(write=False)
    return matrix


def _support_vector(n: int, support: Iterable[int]) -> np.ndarray:
    return _support_matrix(n, [support])[0]


def make_css_code(name: str, n: int, x_checks, z_checks,
                  logical_x: tuple[Iterable[int], Iterable[int]],
                  logical_z: tuple[Iterable[int], Iterable[int]]) -> CssCode:
    """Build a CssCode from qubit-index supports: logicals given as (X support, Z support)."""
    return CssCode(
        name=name,
        x_checks=_support_matrix(n, x_checks),
        z_checks=_support_matrix(n, z_checks),
        logical_x=(_support_vector(n, logical_x[0]), _support_vector(n, logical_x[1])),
        logical_z=(_support_vector(n, logical_z[0]), _support_vector(n, logical_z[1])),
    )


def gf2_rank(matrix: np.ndarray) -> int:
    """Rank over GF(2): how many independent parity checks a check matrix really has."""
    m = matrix.astype(np.uint8) % 2
    rank = 0
    for col in range(m.shape[1]):
        pivots = np.nonzero(m[rank:, col])[0]
        if pivots.size == 0:
            continue
        pivot = rank + pivots[0]
        m[[rank, pivot]] = m[[pivot, rank]]
        for row in range(m.shape[0]):
            if row != rank and m[row, col]:
                m[row] ^= m[rank]
        rank += 1
        if rank == m.shape[0]:
            break
    return rank


def min_undetectable_logical_weight(checks: np.ndarray, logical_parts: Sequence[np.ndarray],
                                    max_weight: int) -> int | None:
    """Fewest same-type Pauli errors that every check misses but that flip a logical.

    That number is the code's distance for this error type. A code of distance d corrects
    any (d - 1) // 2 errors: with d = 3, one error is always nearer the true state than any
    logical flip, so the minimum-weight decoder picks the right fix.
    """
    n = checks.shape[1]
    for weight in range(1, max_weight + 1):
        combos = np.array(list(combinations(range(n), weight)))
        errors = np.zeros((len(combos), n), dtype=np.int64)
        np.put_along_axis(errors, combos, 1, axis=1)
        if checks.shape[0]:
            unseen = ~((errors @ checks.T.astype(np.int64)) % 2).any(axis=1)
        else:
            unseen = np.ones(len(errors), dtype=bool)
        flips = np.zeros(len(errors), dtype=bool)
        for part in logical_parts:
            flips |= ((errors @ part.astype(np.int64)) % 2).astype(bool)
        if (unseen & flips).any():
            return weight
    return None


def validate_code(code: CssCode, expected_distance: int) -> list[Check]:
    """Structural checks on a code definition, independent of any circuit or decoder."""
    xc, zc = code.x_checks.astype(np.int64), code.z_checks.astype(np.int64)
    lx, lz = code.logical_x, code.logical_z
    stabilizers_commute = not ((xc @ zc.T) % 2).any()
    logicals_commute = all(not ((zc @ L[0]) % 2).any() and not ((xc @ L[1]) % 2).any()
                           for L in (lx, lz))
    logicals_anticommute = int(lx[0] @ lz[1] + lx[1] @ lz[0]) % 2 == 1
    k = code.n - gf2_rank(code.x_checks) - gf2_rank(code.z_checks)
    d_x = min_undetectable_logical_weight(code.z_checks, (lx[1], lz[1]), code.n)
    d_z = min_undetectable_logical_weight(code.x_checks, (lx[0], lz[0]), code.n)
    distance = min(d for d in (d_x, d_z) if d is not None)
    return [
        Check(f"{code.name}: stabilizers commute", stabilizers_commute),
        Check(f"{code.name}: logicals commute with every stabilizer", logicals_commute),
        Check(f"{code.name}: logical X and Z anticommute", logicals_anticommute),
        Check(f"{code.name}: encodes exactly one logical qubit", k == 1, f"k = {k}"),
        Check(f"{code.name}: distance = {expected_distance}", distance == expected_distance,
              f"X-type {d_x}, Z-type {d_z}"),
    ]


@dataclass(frozen=True)
class LookupDecoder:
    """Minimum-weight decoder: syndrome integer -> which qubits to flip."""

    x_correction: np.ndarray    # indexed by the Z-check syndrome; rows are X flips
    z_correction: np.ndarray    # indexed by the X-check syndrome; rows are Z flips


def min_weight_table(checks: np.ndarray) -> np.ndarray:
    """For every syndrome, the fewest-qubit error that produces it.

    Breadth-first search over syndromes: start at "no error", and each step flip one more
    qubit. The first time a syndrome is reached, the path that reached it is a minimum-weight
    explanation. For iid noise, fewest errors = most likely errors, so this is the decoder
    matching-based decoders approximate. Cost is (#syndromes x #qubits): 4,096 x 25 at d=5.
    Bit k of a syndrome integer is the outcome of check row k.
    """
    m, n = checks.shape
    table = np.zeros((1 << m, n), dtype=np.uint8)
    reached = np.zeros(1 << m, dtype=bool)
    reached[0] = True
    column_syndrome = [int(sum(int(checks[row, q]) << row for row in range(m)))
                       for q in range(n)]
    frontier = [0]
    while frontier:
        next_frontier = []
        for syndrome in frontier:
            for q in range(n):
                target = syndrome ^ column_syndrome[q]
                if not reached[target]:
                    reached[target] = True
                    table[target] = table[syndrome]
                    table[target, q] ^= 1
                    next_frontier.append(target)
        frontier = next_frontier
    if not reached.all():
        raise ValueError("dependent checks: some syndromes can never occur")
    table.setflags(write=False)
    return table


def build_decoder(code: CssCode) -> LookupDecoder:
    return LookupDecoder(min_weight_table(code.z_checks), min_weight_table(code.x_checks))


def _syndromes(errors: np.ndarray, checks: np.ndarray) -> np.ndarray:
    if checks.shape[0] == 0:
        return np.zeros(len(errors), dtype=np.int64)
    bits = (errors.astype(np.int64) @ checks.T.astype(np.int64)) % 2
    return bits @ (1 << np.arange(checks.shape[0], dtype=np.int64))


def logical_failures(code: CssCode, decoder: LookupDecoder,
                     ex: np.ndarray, ez: np.ndarray) -> np.ndarray:
    """Decode a batch of Pauli errors (rows of ex, ez); True where a logical flipped."""
    rx = ex ^ decoder.x_correction[_syndromes(ex, code.z_checks)]
    rz = ez ^ decoder.z_correction[_syndromes(ez, code.x_checks)]

    def flips(logical: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
        overlap = rx.astype(np.int64) @ logical[1] + rz.astype(np.int64) @ logical[0]
        return (overlap % 2).astype(bool)

    return flips(code.logical_x) | flips(code.logical_z)


def exact_logical_error_curve(code: CssCode, decoder: LookupDecoder,
                              noise: str) -> Callable[[float], float]:
    """Exact logical error rate as a polynomial in p, by enumerating EVERY error pattern.

    Count A_w = number of weight-w patterns the decoder gets wrong. Each specific weight-w
    pattern has probability q^w (1-p)^(n-w), with q = p for one-type noise and q = p/3 for
    depolarizing (a specific Pauli). So P_L(p) = sum_w A_w q^w (1-p)^(n-w), with no
    sampling noise. 4^9 = 262,144 patterns for the 9-qubit codes: well under a second.
    """
    n = code.n
    bits = ((np.arange(1 << n)[:, None] >> np.arange(n)) & 1).astype(np.uint8)
    if noise == "bit_flip":
        ex, ez = bits, np.zeros_like(bits)
    elif noise == "phase_flip":
        ex, ez = np.zeros_like(bits), bits
    elif noise == "depolarizing":
        i, j = np.meshgrid(np.arange(1 << n), np.arange(1 << n), indexing="ij")
        ex, ez = bits[i.ravel()], bits[j.ravel()]
    else:
        raise ValueError(f"unknown noise model {noise!r}")
    failed = logical_failures(code, decoder, ex, ez)
    weights = (ex | ez).sum(axis=1)
    counts = np.bincount(weights[failed], minlength=n + 1)
    per_pauli = 3.0 if noise == "depolarizing" else 1.0

    def curve(p: float) -> float:
        q = p / per_pauli
        return float(sum(counts[w] * q ** w * (1.0 - p) ** (n - w) for w in range(n + 1)))

    return curve


def exact_biased_curve(code: CssCode, decoder: LookupDecoder) -> Callable[[float, float], float]:
    """Exact logical error rate under biased noise, as a function of (p, eta).

    Same enumeration as above, but a pattern's probability now depends on WHICH Paulis it
    holds, not just how many: with a X's, b Y's and c Z's it is
    P(X)^a P(Y)^b P(Z)^c (1-p)^(n-a-b-c). So failures are counted per (a, b, c) instead of
    per weight. 4^n patterns: fine up to n = 9 (the d=3 surface code); d=5 is sampled.
    """
    n = code.n
    bits = ((np.arange(1 << n)[:, None] >> np.arange(n)) & 1).astype(np.uint8)
    i, j = np.meshgrid(np.arange(1 << n), np.arange(1 << n), indexing="ij")
    ex, ez = bits[i.ravel()], bits[j.ravel()]
    failed = logical_failures(code, decoder, ex, ez)
    kinds = np.stack([(ex & (1 - ez)).sum(axis=1), (ex & ez).sum(axis=1),
                      ((1 - ex) & ez).sum(axis=1)], axis=1)[failed]
    patterns, counts = np.unique(kinds, axis=0, return_counts=True)

    def curve(p: float, eta: float) -> float:
        p_x, p_y, p_z = bias_split(p, eta)
        return float(sum(k * p_x ** a * p_y ** b * p_z ** c * (1.0 - p) ** (n - a - b - c)
                         for (a, b, c), k in zip(patterns.tolist(), counts.tolist())))

    return curve


def sampled_biased_error_rate(code: CssCode, decoder: LookupDecoder, p: float, eta: float,
                              samples: int, rng: np.random.Generator) -> float:
    """Monte Carlo version of exact_biased_curve, for the d=5 surface code."""
    p_x, p_y, _ = bias_split(p, eta)
    u = rng.random((samples, code.n))
    ex = (u < p_x + p_y).astype(np.uint8)                 # X (u < p_x) or Y (next p_y)
    ez = ((u >= p_x) & (u < p)).astype(np.uint8)          # Y or Z (the rest of p)
    return float(logical_failures(code, decoder, ex, ez).mean())


def sampled_logical_error_rate(code: CssCode, decoder: LookupDecoder, p: float,
                               samples: int, rng: np.random.Generator) -> float:
    """Monte Carlo version for codes too large to enumerate (depolarizing noise)."""
    u = rng.random((samples, code.n))
    ex = (u < 2 * p / 3).astype(np.uint8)                 # X (u < p/3) or Y (p/3 <= u < 2p/3)
    ez = ((u >= p / 3) & (u < p)).astype(np.uint8)        # Y or Z (2p/3 <= u < p)
    return float(logical_failures(code, decoder, ex, ez).mean())


def break_even_point(curve: Callable[[float], float], lo: float = 1e-3,
                     hi: float = 0.5) -> float | None:
    """Smallest p in (lo, hi] where the encoded qubit stops beating the bare one.

    Returns None when the curve never crosses the diagonal in range. That covers BOTH a code
    that is always worse and one that is always better, so a caller that needs to know
    which must test curve(p) > p directly.
    """
    def gap(p: float) -> float:
        return curve(p) - p                           # negative = encoding helps

    grid = np.linspace(lo, hi, 500)
    values = [gap(p) for p in grid]
    if values[0] > 0:
        return None
    if abs(values[0]) < 1e-12:
        return float(lo)
    for a, b, va, vb in zip(grid, grid[1:], values, values[1:]):
        if abs(vb) < 1e-12:                           # lands exactly on the diagonal
            return float(b)
        if va < 0 < vb:
            return float(brentq(gap, a, b))
    return None
