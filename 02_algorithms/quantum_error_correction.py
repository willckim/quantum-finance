"""Quantum error correction from the ground up -- a learning project.

WHAT THIS SCRIPT BUILDS
-----------------------
Six stages. Each one fixes the weakness the stage before it exposed.

    Part 1  The problem        One bare qubit under bit-flip noise is wrong with probability p.
    Part 2  3-qubit code       Spread one qubit across three. Parity checks find a bit flip
                               without ever reading the stored value.
    Part 3  Phase flips        The bit-flip code is blind to phase errors (shown failing);
                               its Hadamard-rotated twin catches them.
    Part 4  Shor's 9-qubit     Nest the two codes. Corrects X, Y or Z on any one qubit.
    Part 5  Surface code       Distance-3 rotated surface code: 9 data qubits on a grid,
                               8 local stabilizer checks, syndromes drawn on the lattice.
    Part 6  Threshold chart    error_correction_threshold.png: logical vs physical error rate.

THE FIVE IDEAS THIS SCRIPT IS REALLY ABOUT
------------------------------------------
1. NO-CLONING. Classical memory fights noise by keeping copies and voting. A qubit cannot
   be copied: no physical process turns an unknown a|0> + b|1> into two independent copies
   of itself (Wootters & Zurek; Dieks, 1982). Encoding does not copy the STATE; it spreads
   the AMPLITUDES: a|0> + b|1>  ->  a|000> + b|111>. That is one entangled state, not three
   copies. (Three copies would be (a|0> + b|1>) x 3, a different state entirely.)

2. PHYSICAL vs LOGICAL QUBIT. A physical qubit is one piece of hardware (a transmon, an ion).
   A logical qubit is the single qubit of information stored collectively across many
   physical qubits. Algorithms run on logical qubits; noise hits physical ones.

3. SYNDROME MEASUREMENT (the conceptual heart). Measuring a data qubit asks "are you 0 or 1?"
   and destroys the superposition. A syndrome measurement asks only "do these two qubits
   AGREE?" Both |000> and |111> answer "yes", so the answer carries no information about
   a or b and nothing collapses. An error changes the answer, and the pattern of answers
   (the syndrome) points at the culprit. Part 2 verifies this with state fidelity.

4. THRESHOLD. Encoding adds qubits, and every added qubit is another place for errors to
   land. Correction only wins when the physical error rate is below a critical value.
   Two versions of that idea appear here and should not be confused:
     * BREAK-EVEN (pseudo-threshold): the p where ONE code's logical error equals p.
       (Experiments also say "break-even" for beating the best physical qubit's lifetime,
       as Google's Willow did by 2.4x; "pseudo-threshold" is the precise term here.)
     * THRESHOLD: the p below which making the code BIGGER makes it BETTER. Below it,
       error suppression compounds with size; above it, bigger codes are worse.
   Part 6 plots both.

5. OVERHEAD. Protection is bought with qubits. A surface code of distance d spends
   2d^2 - 1 physical qubits per logical qubit (17 at d=3, 97 at d=7, about 1,000 at the
   distances useful algorithms need). That multiplier, not the qubit count on a press
   release, is the central engineering problem. Part 6 prints the arithmetic.

NOISE MODEL -- READ THIS BEFORE TRUSTING ANY NUMBER
---------------------------------------------------
All sweeps use CODE-CAPACITY noise: errors strike the data qubits once, between encoding
and syndrome extraction. The parity checks, corrections and readout are perfect. This is
the textbook model for comparing codes, because it isolates what the CODE can do.

Real hardware is harsher: the ancilla qubits, the CNOTs and the measurements that do the
checking fail too. Under that CIRCUIT-LEVEL noise, the surface code threshold falls from
about 10-15% (code capacity) to roughly 1%. The numbers printed here are therefore upper
bounds on what these codes achieve in practice, and the script says so where it prints them.

WHAT IS HAND-BUILT, WHAT IS SIMPLIFIED, AND WHAT WAS SUBSTITUTED
----------------------------------------------------------------
* qiskit-qec, stim and pymatching are NOT installed (checked for Qiskit 2.3.1 / Aer 0.17.2).
  Every encoder, syndrome circuit, lookup decoder and lattice here is hand-built.
* One exception: the surface code's logical-state preparation is SYNTHESIZED from its
  stabilizer list with qiskit.quantum_info (a general Clifford synthesis tool, not a QEC
  utility). That prep circuit is an ideal, non-local scoring device, not the physics
  under test. Hardware prepares surface-code states by measuring stabilizers instead.
* Corrections are applied inside the circuit with if_test so every step is visible.
  Real hardware usually tracks corrections in software (a "Pauli frame") instead.
* The distance-5 surface code (49 qubits) is simulated with a classical Pauli-frame
  sampler, the same technique Stim uses. At distance 3 the same model is first checked
  against the full Qiskit circuits (both must match an exact enumeration).

WHY A CLASSICAL COMPUTER CAN SIMULATE ALL OF THIS
-------------------------------------------------
Every gate used in error correction (H, S, CNOT, Pauli errors, measurement) is a Clifford
operation. The Gottesman-Knill theorem says Clifford circuits can be simulated efficiently
by tracking stabilizers instead of 2^n amplitudes. Aer's "stabilizer" method does exactly
that, which is why an 18-qubit circuit with mid-circuit measurement runs thousands of shots
per second here (the statevector method is about 70x slower on the same circuit). The two
exceptions, arbitrary input states in Parts 2-3 and one non-Clifford rotation in Part 4,
are small enough to run on the statevector method instead.

Run:  py quantum_error_correction.py      (writes error_correction_threshold.png next to it)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence

import matplotlib
matplotlib.use("Agg")  # write the PNG without needing a display
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import brentq

import qiskit
import qiskit_aer
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.circuit import Qubit
from qiskit.quantum_info import (DensityMatrix, Operator, Pauli, StabilizerState,
                                 Statevector, partial_trace, state_fidelity)
from qiskit_aer import AerSimulator
from qiskit_aer.noise import pauli_error

# ----------------------------- configuration -------------------------------
SEED = 2026
SHOTS = 8_000                                   # shots per point in every Aer sweep
P_SWEEP_CIRCUIT = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50)   # Aer circuit points
P_SWEEP_EXACT = np.linspace(0.0, 0.5, 251)      # dense grid for exact curves
P_SWEEP_LOG = np.geomspace(0.01, 0.30, 15)      # log grid for the threshold panel
SURFACE_D5_SAMPLES = 400_000                    # Pauli-frame samples per point at d=5
Z_SCORE_LIMIT = 4.0     # |simulated - exact| must sit within 4 standard errors. About 50
                        # random comparisons are made, so the chance of any false alarm is
                        # ~0.3%. The price: at low p the band is wide (+-50% of the 3-qubit
                        # rate at p = 0.05), so sweeps mainly test mid and high p; the exact
                        # single-error tables cover the low-p behaviour.
OUTPUT_PNG = Path(__file__).resolve().with_name("error_correction_threshold.png")

# Aer's stabilizer method: exact for Clifford circuits, polynomial cost (Gottesman-Knill).
STABILIZER_SIM = AerSimulator(method="stabilizer")
# Statevector method: needed only when a demo uses non-Clifford rotations (arbitrary states).
STATEVECTOR_SIM = AerSimulator(method="statevector")

Qubits = Sequence[Qubit | int]       # a register, a slice of one, or plain qubit indices


# ======================================================================================
# SECTION 0 -- SHARED TOOLS
# ======================================================================================

@dataclass(frozen=True)
class Check:
    """One self-verification result. The script exits non-zero if any check fails."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class Sweep:
    """Logical error rates measured by running real circuits on the Aer simulator."""

    label: str
    p: tuple[float, ...]
    rate: tuple[float, ...]
    shots: int


def section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


# ---- noise channels --------------------------------------------------------------------
# Aer lets a QuantumError sit directly in a circuit as an instruction. Each shot, Aer samples
# one Pauli from the list with the given probabilities and applies it right there. That puts
# the noise exactly where the comments say it is, with no gate-name matching in a NoiseModel.
#
# GOTCHA: qiskit_aer.noise.depolarizing_error(lam, 1) does NOT fail with probability lam.
# Its X, Y and Z each get lam/4, so the total error probability is 3*lam/4. Building the
# channel from pauli_error below makes p mean "probability that ANY error happens".

def bit_flip_channel(p: float):
    return pauli_error([("X", p), ("I", 1.0 - p)])


def phase_flip_channel(p: float):
    return pauli_error([("Z", p), ("I", 1.0 - p)])


def depolarizing_channel(p: float):
    """X, Y or Z each with probability p/3: the least-structured single-qubit noise."""
    return pauli_error([("X", p / 3), ("Y", p / 3), ("Z", p / 3), ("I", 1.0 - p)])


def inject_noise(qc: QuantumCircuit, qubits: Iterable[Qubit | int], channel) -> None:
    qc.barrier(label="noise")
    for q in qubits:
        qc.append(channel, [q])
    qc.barrier()


def inject_paulis(qc: QuantumCircuit, qubits: Qubits, errors: dict[int, str]) -> None:
    """Deterministic error injection for demos: {data index: "X" | "Y" | "Z"}."""
    qc.barrier(label="error")
    for index, letter in errors.items():
        getattr(qc, letter.lower())(qubits[index])
    qc.barrier()


# ---- running circuits and reading results ----------------------------------------------
def seed_for(*tags: int) -> int:
    """An independent simulator seed for each (part, point) tag.

    GOTCHA: Aer seeds shot i of a run with seed_simulator + i. Runs seeded s and s + 1
    replay the SAME random stream shifted by one shot, so a sweep seeded SEED, SEED + 1,
    ... reuses essentially one sample at every point: every rate errs in the same
    direction, and agreement checks still pass while testing far less than they claim.
    (Measured here: 40 consecutive-seed runs varied 28x less than independent runs.)
    Hashing the tags through SeedSequence spreads seeds across the 31-bit range instead.
    """
    return int(np.random.SeedSequence([SEED, *tags]).generate_state(1)[0] % (2 ** 31))


def run_counts(qc: QuantumCircuit, shots: int, seed: int,
               simulator: AerSimulator = STABILIZER_SIM) -> dict[str, int]:
    return simulator.run(qc, shots=shots, seed_simulator=seed).result().get_counts()


def register_values(counts_key: str, qc: QuantumCircuit) -> dict[str, int]:
    """Split an Aer counts key into {register name: integer value}.

    GOTCHA: registers appear space-separated with the LAST-added register FIRST, and inside
    each register bit 0 is the RIGHTMOST character. Getting this backwards silently swaps
    syndromes, so every part of the script reads results through this one function.
    """
    parts = counts_key.split()
    if len(parts) != len(qc.cregs):
        raise ValueError(f"counts key {counts_key!r} does not match {len(qc.cregs)} registers")
    return {reg.name: int(bits, 2) for reg, bits in zip(reversed(qc.cregs), parts)}


def failure_rate(counts: dict[str, int], qc: QuantumCircuit,
                 is_failure: Callable[[dict[str, int]], bool]) -> float:
    shots = sum(counts.values())
    bad = sum(n for key, n in counts.items() if is_failure(register_values(key, qc)))
    return bad / shots


def z_score(observed: float, expected: float, shots: int) -> float:
    """Distance between a simulated rate and the exact value, in binomial standard errors."""
    std_err = np.sqrt(expected * (1.0 - expected) / shots)
    if std_err == 0.0:
        return 0.0 if observed == expected else float("inf")
    return abs(observed - expected) / std_err


def agreement_check(name: str, sweep: Sweep, exact: Callable[[float], float]) -> Check:
    scores = [z_score(r, exact(p), sweep.shots) for p, r in zip(sweep.p, sweep.rate)]
    worst = max(scores)
    return Check(name, worst <= Z_SCORE_LIMIT,
                 f"worst |circuit - exact| = {worst:.2f} standard errors "
                 f"over {len(scores)} points")


def parity_pair(value: int) -> str:
    """Render a 2-bit syndrome as the two parity answers, check 0 first."""
    return f"{value & 1}{(value >> 1) & 1}"


def percent(value: float | None, digits: int = 2, missing: str = "never") -> str:
    """Format an optional rate. None means two curves never crossed in the range searched."""
    return missing if value is None else f"{value:.{digits}%}"


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


# ======================================================================================
# PART 1 -- THE PROBLEM: A BARE QUBIT HAS NO DEFENSE
# ======================================================================================

def bare_qubit_circuit(p: float) -> QuantumCircuit:
    """Prepare the known state |1>, expose it to bit-flip noise, measure it."""
    qc = QuantumCircuit(QuantumRegister(1, "q"), ClassicalRegister(1, "out"), name="bare")
    qc.x(0)
    inject_noise(qc, [0], bit_flip_channel(p))
    qc.measure(0, 0)
    return qc


def part1_bare_qubit() -> tuple[Sweep, list[Check]]:
    section("PART 1 -- THE PROBLEM: one bare qubit under bit-flip noise")
    print("Prepare |1>. Each shot, noise flips it to |0> with probability p. Measure.\n")
    print(f"  {'p':>5}   {'wrong readouts':>14}   {'expected':>8}")
    rates = []
    for i, p in enumerate(P_SWEEP_CIRCUIT):
        qc = bare_qubit_circuit(p)
        counts = run_counts(qc, SHOTS, seed_for(1, i))
        rate = failure_rate(counts, qc, lambda reg: reg["out"] == 0)
        rates.append(rate)
        print(f"  {p:>5.2f}   {rate:>14.4f}   {p:>8.4f}")
    sweep = Sweep("bare qubit", P_SWEEP_CIRCUIT, tuple(rates), SHOTS)
    print("\n  Baseline: with no protection the logical error rate IS the physical rate p.")
    print("  Every code below must land under this diagonal to earn its extra qubits.")
    return sweep, [agreement_check("Part 1: bare-qubit error rate equals p", sweep, lambda p: p)]


# ======================================================================================
# PART 2 -- THE 3-QUBIT REPETITION CODE (BIT-FLIP CORRECTION)
# ======================================================================================
# WHY NOT JUST COPY THE QUBIT?
#   Classical memory survives noise by storing 0 as 000 and voting. The quantum version of
#   "make three copies" is impossible: the no-cloning theorem forbids any circuit that maps
#   an UNKNOWN state a|0> + b|1> to (a|0> + b|1>)(a|0> + b|1>). Cloning would need to know
#   a and b, and learning them requires measuring, which destroys them.
#
# WHAT ENCODING DOES INSTEAD
#   Two CNOTs from the data qubit onto two fresh |0> qubits give
#
#       (a|0> + b|1>) |00>   --CNOT, CNOT-->   a|000> + b|111>
#
#   Nothing was copied. The amplitudes a and b still appear exactly once, now attached to
#   the joint patterns 000 and 111. Those three PHYSICAL qubits jointly hold one LOGICAL
#   qubit: |0_L> = |000>, |1_L> = |111>. Measure any one of them and you would see a
#   random bit (0 with probability |a|^2) and the superposition would be gone, so the
#   code must never look at them one at a time.
#
# SYNDROME MEASUREMENT: ASK "DO YOU AGREE?", NEVER "WHAT ARE YOU?"
#   Two ancilla qubits start in |0>. CNOTs from data 0 and data 1 onto ancilla 0 leave it
#   holding (d0 XOR d1); ancilla 1 gets (d1 XOR d2). In stabilizer language these are the
#   measurements of Z0Z1 and Z1Z2.
#
#   The key fact: in BOTH |000> and |111>, every pair agrees, so both parities are 0. The
#   ancillas come out in the same state for every term of the superposition, which means
#   they carry zero information about a or b and measuring them collapses nothing. If one
#   qubit has flipped, say data 1, BOTH terms become 010 and 101, both parities read 1 in
#   both terms, and again the answer is identical across the superposition. The syndrome
#   reveals WHERE the error is, never WHAT the data is.
#
#       syndrome (d0^d1, d1^d2)   meaning             fix
#              0 0                no flip              nothing
#              1 0                data 0 disagrees     X on data 0
#              1 1                data 1 disagrees     X on data 1
#              0 1                data 2 disagrees     X on data 2
#
#   Two or more flips fool it: 110 looks like "data 2 flipped" from 111, and the "fix"
#   completes the logical flip to 111. That failure needs two independent errors, so it
#   happens with probability ~3p^2 instead of p. For small p, p^2 is much smaller than p.
#   That squaring is the entire trick of error correction.

# In [[n, k, d]] notation this is [[3,1,1]]: distance 3 against bit flips (d_X = 3), but
# distance 1 overall, because a single Z on any qubit is already a logical error (Part 3).
REP3_CORRECTION = {0b01: 0, 0b11: 1, 0b10: 2}      # syndrome register value -> qubit to fix
REP3_CODE = make_css_code("3-qubit bit-flip code", 3, x_checks=[], z_checks=[(0, 1), (1, 2)],
                          logical_x=([0, 1, 2], []), logical_z=([], [0]))


def rep3_closed_form(p: float) -> float:
    """Textbook failure rate of a 3-qubit repetition code against its own error type:
    the chance that 2 or 3 of the 3 qubits flipped, 3p^2(1-p) + p^3 = 3p^2 - 2p^3."""
    return 3 * p ** 2 - 2 * p ** 3


def encode_repetition(qc: QuantumCircuit, data: Qubits) -> None:
    qc.cx(data[0], data[1])
    qc.cx(data[0], data[2])


def decode_repetition(qc: QuantumCircuit, data: Qubits) -> None:
    qc.cx(data[0], data[2])
    qc.cx(data[0], data[1])


def measure_bit_parities(qc: QuantumCircuit, data: Qubits, anc: Qubits,
                         syndrome: ClassicalRegister) -> None:
    """Measure Z0Z1 and Z1Z2 onto two ancillas. The data qubits are never measured."""
    qc.cx(data[0], anc[0])
    qc.cx(data[1], anc[0])          # anc 0 = d0 XOR d1
    qc.cx(data[1], anc[1])
    qc.cx(data[2], anc[1])          # anc 1 = d1 XOR d2
    qc.measure(anc[0], syndrome[0])
    qc.measure(anc[1], syndrome[1])


def correct_bit_flip(qc: QuantumCircuit, data: Qubits, syndrome: ClassicalRegister) -> None:
    """Feed-forward: apply X to the qubit the syndrome points at, decided mid-circuit.

    GOTCHA: Qiskit 2.x removed Instruction.c_if. Classically controlled gates are written
    with the if_test context manager, which Aer's stabilizer method supports.
    """
    for value, qubit in REP3_CORRECTION.items():
        with qc.if_test((syndrome, value)):
            qc.x(data[qubit])


# Parts 2 and 3 share one layout: data 0..2, ancilla 3..4 (circuit qubit indices).
EncodeFn = Callable[[QuantumCircuit, Qubits], None]
MeasureFn = Callable[[QuantumCircuit, Qubits, Qubits, ClassicalRegister], None]
CorrectFn = Callable[[QuantumCircuit, Qubits, ClassicalRegister], None]
CodeOps = tuple[EncodeFn, MeasureFn, CorrectFn, EncodeFn]     # encode, measure, correct, decode
BIT_FLIP_OPS: CodeOps = (encode_repetition, measure_bit_parities, correct_bit_flip,
                         decode_repetition)


def three_qubit_layout(*extra: ClassicalRegister) -> tuple[QuantumCircuit, QuantumRegister,
                                                           QuantumRegister, ClassicalRegister]:
    data, anc = QuantumRegister(3, "data"), QuantumRegister(2, "anc")
    syndrome = ClassicalRegister(2, "syndrome")
    return QuantumCircuit(data, anc, syndrome, *extra), data, anc, syndrome


def bit_flip_code_memory(p: float) -> QuantumCircuit:
    """Store logical |1> for one round of bit-flip noise, correct it, read it back."""
    logical = ClassicalRegister(1, "logical")
    qc, data, anc, syndrome = three_qubit_layout(logical)
    qc.x(data[0])                                   # logical |1>, same state as Part 1
    encode_repetition(qc, data)
    inject_noise(qc, data, bit_flip_channel(p))
    measure_bit_parities(qc, data, anc, syndrome)
    correct_bit_flip(qc, data, syndrome)
    decode_repetition(qc, data)
    qc.measure(data[0], logical[0])
    return qc


def arbitrary_state(theta: float, phi: float) -> QuantumCircuit:
    """A lopsided, complex-phased single-qubit state: nothing special about 0 or 1."""
    qc = QuantumCircuit(1, name="psi")
    qc.ry(theta, 0)
    qc.rz(phi, 0)
    return qc


DEMO_STATES = {"psi_A": arbitrary_state(1.1, 0.7), "psi_B": arbitrary_state(2.5, -1.9)}


def single_error_run(prepare: QuantumCircuit, ops: CodeOps,
                     errors: dict[int, str]) -> tuple[int, float]:
    """Encode `prepare`, inject specific errors, run one correction cycle, decode.

    Returns (syndrome value, fidelity of the decoded qubit with the original state). The
    fidelity comes from the full statevector, so it checks the amplitudes a AND b including
    their relative phase, not just a measurement in one basis.
    """
    encode, measure, correct, decode = ops
    qc, data, anc, syndrome = three_qubit_layout()
    qc.compose(prepare, [data[0]], inplace=True)
    encode(qc, data)
    inject_paulis(qc, data, errors)
    measure(qc, data, anc, syndrome)
    correct(qc, data, syndrome)
    decode(qc, data)
    qc.save_statevector()
    result = STATEVECTOR_SIM.run(qc, shots=1, seed_simulator=SEED).result()
    value = register_values(next(iter(result.get_counts())), qc)["syndrome"]
    decoded = partial_trace(result.get_statevector(), [1, 2, 3, 4])   # keep data 0 only
    return value, float(state_fidelity(decoded, Statevector(prepare)))


def single_error_table(ops: CodeOps, letter: str, expect_fixed: bool,
                       label: str) -> list[Check]:
    """Print syndrome and fidelity for no error and one error on each data qubit."""
    print(f"  {'injected':>12}   {'syndrome psi_A':>14}   {'syndrome psi_B':>14}   "
          f"{'fidelity psi_A':>14}   {'fidelity psi_B':>14}")
    cases = [("none", {})] + [(f"{letter} on data {q}", {q: letter}) for q in range(3)]
    worst_fidelity, syndromes_match = 1.0, True
    for name, errors in cases:
        (s_a, f_a), (s_b, f_b) = (single_error_run(state, ops, errors)
                                  for state in DEMO_STATES.values())
        worst_fidelity = min(worst_fidelity, f_a, f_b)
        syndromes_match &= s_a == s_b
        print(f"  {name:>12}   {parity_pair(s_a):>14}   {parity_pair(s_b):>14}   "
              f"{f_a:>14.6f}   {f_b:>14.6f}")
    fixed = worst_fidelity > 1 - 1e-9
    return [
        Check(f"{label}: every single {letter} error leaves fidelity 1" if expect_fixed
              else f"{label}: {letter} errors are NOT corrected (expected failure)",
              fixed == expect_fixed, f"worst fidelity {worst_fidelity:.6f}"),
        Check(f"{label}: syndrome identical for different logical states", syndromes_match,
              "the syndrome carries no information about a or b"),
    ]


def fidelity_after_direct_measurement(prepare: QuantumCircuit) -> tuple[float, float]:
    """The wrong way to check for errors: measure data qubit 1 directly.

    An unrecorded Z measurement of data 1 keeps both collapse branches (P0 rho P0 +
    P1 rho P1). Returns (fidelity from simulation, closed form |a|^4 + |b|^4).
    """
    encoder = QuantumCircuit(3)
    encoder.compose(prepare, [0], inplace=True)
    encode_repetition(encoder, encoder.qubits)
    rho = DensityMatrix(Statevector(encoder))
    branches = [rho.evolve(Operator.from_label(label)) for label in ("I0I", "I1I")]
    measured = DensityMatrix(branches[0].data + branches[1].data)
    decoder = QuantumCircuit(3)
    decode_repetition(decoder, decoder.qubits)
    decoded = partial_trace(measured.evolve(decoder), [1, 2])
    prob_zero = abs(Statevector(prepare).data[0]) ** 2
    return (float(state_fidelity(decoded, Statevector(prepare))),
            prob_zero ** 2 + (1 - prob_zero) ** 2)


def print_error_rate_sweep(rows: Sequence[tuple[str, Sweep, Callable[[float], float]]]) -> None:
    """Table of circuit-measured vs exact logical error rates, one column pair per code."""
    header = "".join(f"   {name + ' circuit / exact':>28}" for name, _, _ in rows)
    print(f"  {'p':>5}{header}")
    for i, p in enumerate(rows[0][1].p):
        cells = "".join(f"   {sweep.rate[i]:>13.4f} / {exact(p):<12.4f}"
                        for _, sweep, exact in rows)
        print(f"  {p:>5.2f}{cells}")


def run_sweep(label: str, build: Callable[[float], QuantumCircuit],
              is_failure: Callable[[dict[str, int]], bool], seed_tag: int) -> Sweep:
    rates = []
    for i, p in enumerate(P_SWEEP_CIRCUIT):
        qc = build(p)
        rates.append(failure_rate(run_counts(qc, SHOTS, seed_for(seed_tag, i)), qc,
                                  is_failure))
    return Sweep(label, P_SWEEP_CIRCUIT, tuple(rates), SHOTS)


def part2_bit_flip_code() -> tuple[Sweep, Callable[[float], float], list[Check]]:
    section("PART 2 -- THE 3-QUBIT REPETITION CODE: correcting bit flips")
    checks = validate_code(REP3_CODE, expected_distance=1)
    print("One cycle: encode -> noise -> measure 2 parities -> correct -> decode -> read.\n")
    print(bit_flip_code_memory(0.1).draw(output="text", fold=-1))

    print("\n(a) The syndrome finds the error without collapsing the state.")
    print("    Two arbitrary superpositions, one deliberate X error at a time. Fidelity 1.000000")
    print("    means the decoded qubit matches the original in amplitude AND phase.\n")
    checks += single_error_table(BIT_FLIP_OPS, "X", expect_fixed=True, label="Part 2")

    simulated, closed_form = fidelity_after_direct_measurement(DEMO_STATES["psi_A"])
    print("\n(b) Contrast: 'checking' by measuring data qubit 1 directly, with no error at all.")
    print(f"    Fidelity afterwards: {simulated:.6f} (closed form |a|^4 + |b|^4 = "
          f"{closed_form:.6f}).")
    print("    Looking at the data destroyed the superposition. The parity checks did not.")
    checks.append(Check("Part 2: direct measurement collapses the state as predicted",
                        abs(simulated - closed_form) < 1e-9 and simulated < 0.99,
                        f"fidelity {simulated:.6f}"))

    bfs_table = min_weight_table(REP3_CODE.z_checks)
    hand_table = {s: int(np.flatnonzero(bfs_table[s])[0]) for s in range(1, 4)}
    checks.append(Check("Part 2: hand-written syndrome table = min-weight decoder",
                        hand_table == REP3_CORRECTION, str(hand_table)))

    sweep = run_sweep("3-qubit code", bit_flip_code_memory,
                      lambda reg: reg["logical"] == 0, seed_tag=200)
    enumerated = exact_logical_error_curve(REP3_CODE, build_decoder(REP3_CODE), "bit_flip")
    gap = max(abs(rep3_closed_form(p) - enumerated(p)) for p in P_SWEEP_EXACT)
    checks += [agreement_check("Part 2: circuit sweep matches 3p^2 - 2p^3", sweep,
                               rep3_closed_form),
               Check("Part 2: Pauli-frame enumeration reproduces 3p^2 - 2p^3", gap < 1e-12,
                     f"max difference {gap:.1e}")]

    print("\n(c) Sweep: logical error rate after one correction cycle, bit-flip noise.")
    print("    exact = 3p^2 - 2p^3, the chance that 2 or 3 of the 3 qubits flipped.\n")
    print_error_rate_sweep([("3-qubit code", sweep, rep3_closed_form)])
    p_star = break_even_point(enumerated)
    checks.append(Check("Part 2: break-even of 3-qubit code is exactly p = 0.5",
                        p_star is not None and abs(p_star - 0.5) < 1e-9, f"p* = {p_star}"))
    print(f"\n  KEY RESULT: break-even at p* = {percent(p_star, 1)}. Solving 3p^2 - 2p^3 = p gives")
    print("  p = 0, 1/2 or 1, so the encoded qubit beats the bare one at EVERY p below 50%")
    print("  and loses above it (at p = 0.6 the code fails 64.8% of the time vs 60.0% bare).")
    print("  Why so generous? This model gives the code PERFECT parity checks. On hardware the")
    print("  ancillas, CNOTs and measurements fail too, and a lying syndrome turns the 'fix'")
    print("  into an error. That is what drags real thresholds down toward 1% (see Part 6).")
    return sweep, enumerated, checks


# ======================================================================================
# PART 3 -- PHASE FLIPS: THE ERROR THE BIT-FLIP CODE CANNOT SEE
# ======================================================================================
# A qubit can go wrong in a way no classical bit can. A PHASE FLIP (the Z gate) leaves
# |0> and |1> alone but flips the sign between them:
#
#       a|0> + b|1>   --Z-->   a|0> - b|1>            (so |+> becomes |->)
#
# Measure in the 0/1 basis and nothing looks different, yet the state is now orthogonal
# to |+>. Real qubits suffer this constantly (dephasing is usually the FASTEST noise).
#
# WHY THE BIT-FLIP CODE IS BLIND TO IT
#   Z on any one qubit of a|000> + b|111> gives a|000> - b|111>. Every pair still agrees,
#   so the syndrome is 00 and nothing is fixed. Worse, Z on ANY of the three qubits causes
#   the same logical flip, so the code roughly TRIPLES the phase-error rate (about 3p).
#
# THE FIX: CHANGE BASIS WITH HADAMARDS
#   H swaps the roles of X and Z (H X H = Z). In the |+>/|-> basis a phase flip IS a bit
#   flip, so run the same code in that basis: encode as before, then H every qubit.
#       |0_L> = |+++>,  |1_L> = |--->
#   The checks become X0X1 and X1X2 (rotate with H, compare bit parities, rotate back).
#   The catch is the mirror image: now X errors slip through. Neither code alone survives
#   noise that contains both kinds, and real noise always does. That motivates Part 4.

PHASE_CODE = make_css_code("3-qubit phase-flip code", 3, x_checks=[(0, 1), (1, 2)],
                           z_checks=[], logical_x=([], [0, 1, 2]), logical_z=([0], []))


def odd_flip_closed_form(p: float) -> float:
    """Chance that an odd number of the 3 qubits flipped. That is the bit-flip code's
    failure rate under phase-flip noise: any odd number of Z errors is a logical Z."""
    return (1 - (1 - 2 * p) ** 3) / 2


def encode_phase_flip(qc: QuantumCircuit, data: Qubits) -> None:
    encode_repetition(qc, data)
    for q in data:
        qc.h(q)


def decode_phase_flip(qc: QuantumCircuit, data: Qubits) -> None:
    for q in data:
        qc.h(q)
    decode_repetition(qc, data)


def measure_phase_parities(qc: QuantumCircuit, data: Qubits, anc: Qubits,
                           syndrome: ClassicalRegister) -> None:
    """Measure X0X1 and X1X2: rotate into the Hadamard basis, compare parities, rotate back."""
    for q in data:
        qc.h(q)
    measure_bit_parities(qc, data, anc, syndrome)
    for q in data:
        qc.h(q)


def correct_phase_flip(qc: QuantumCircuit, data: Qubits, syndrome: ClassicalRegister) -> None:
    for value, qubit in REP3_CORRECTION.items():
        with qc.if_test((syndrome, value)):
            qc.z(data[qubit])


PHASE_FLIP_OPS: CodeOps = (encode_phase_flip, measure_phase_parities, correct_phase_flip,
                           decode_phase_flip)


def plus_state() -> QuantumCircuit:
    qc = QuantumCircuit(1, name="plus")
    qc.h(0)
    return qc


def phase_noise_memory(code: str, p: float) -> QuantumCircuit:
    """Run one memory cycle under phase-flip noise; the readout is 1 on a logical failure.

    code is "bare", "bit_flip" or "phase_flip". The probe state has to be one that the
    failure can actually disturb, and that differs by code:
      * bare qubit and bit-flip code: a Z error that gets through acts as a LOGICAL Z,
        which does nothing to |0> or |1>. Probe with |+> and read out in the X basis.
      * phase-flip code: two Z errors fool it into completing Z on all three qubits,
        which maps |+++> to |---> -- a LOGICAL X. That leaves |+_L> unchanged, so probe
        with |0> and read out in the Z basis instead.
    Part 4 removes the need to choose a probe at all by scoring against a reference qubit.
    """
    logical = ClassicalRegister(1, "logical")
    qc, data, anc, syndrome = three_qubit_layout(logical)
    probe_plus = code != "phase_flip"
    if probe_plus:
        qc.h(data[0])
    if code == "bare":
        inject_noise(qc, [data[0]], phase_flip_channel(p))
    else:
        encode, measure, correct, decode = BIT_FLIP_OPS if code == "bit_flip" else PHASE_FLIP_OPS
        encode(qc, data)
        inject_noise(qc, data, phase_flip_channel(p))
        measure(qc, data, anc, syndrome)
        correct(qc, data, syndrome)
        decode(qc, data)
    if probe_plus:
        qc.h(data[0])
    qc.measure(data[0], logical[0])
    return qc


def part3_phase_flips() -> tuple[Callable[[float], float], list[Check]]:
    section("PART 3 -- PHASE FLIPS: the error the bit-flip code cannot see")
    checks = validate_code(PHASE_CODE, expected_distance=1)

    syndrome, fidelity = single_error_run(plus_state(), BIT_FLIP_OPS, {1: "Z"})
    print("(a) Bit-flip code, input |+>, one Z error on data 1:")
    print(f"    syndrome {parity_pair(syndrome)} (nothing detected), fidelity with |+> = "
          f"{fidelity:.6f}")
    print("    The code reported 'all clear' and handed back |->, the orthogonal state.")
    checks.append(Check("Part 3: bit-flip code misses a Z error on |+> completely",
                        syndrome == 0 and fidelity < 1e-9, f"fidelity {fidelity:.6f}"))

    print("\n(b) Bit-flip code vs Z on each qubit, arbitrary states (expect FAILURE):\n")
    checks += single_error_table(BIT_FLIP_OPS, "Z", expect_fixed=False,
                                 label="Part 3 bit-flip code")
    print("\n(c) Hadamard-conjugated phase-flip code vs the same Z errors (expect SUCCESS):\n")
    checks += single_error_table(PHASE_FLIP_OPS, "Z", expect_fixed=True,
                                 label="Part 3 phase-flip code")
    print("\n(d) ...and the mirror image: the phase-flip code vs X errors (expect FAILURE):\n")
    checks += single_error_table(PHASE_FLIP_OPS, "X", expect_fixed=False,
                                 label="Part 3 phase-flip code")

    sweeps = {code: run_sweep(code, lambda p, c=code: phase_noise_memory(c, p),
                              lambda reg: reg["logical"] == 1, seed_tag=300 + k)
              for k, code in enumerate(("bare", "bit_flip", "phase_flip"))}
    enum_bit = exact_logical_error_curve(REP3_CODE, build_decoder(REP3_CODE), "phase_flip")
    enum_phase = exact_logical_error_curve(PHASE_CODE, build_decoder(PHASE_CODE), "phase_flip")
    gap = max(max(abs(enum_bit(p) - odd_flip_closed_form(p)),
                  abs(enum_phase(p) - rep3_closed_form(p))) for p in P_SWEEP_EXACT)
    checks += [
        agreement_check("Part 3: bare |+> phase-error rate equals p", sweeps["bare"], lambda p: p),
        agreement_check("Part 3: bit-flip code under Z noise matches (1-(1-2p)^3)/2",
                        sweeps["bit_flip"], odd_flip_closed_form),
        agreement_check("Part 3: phase-flip code under Z noise matches 3p^2 - 2p^3",
                        sweeps["phase_flip"], rep3_closed_form),
        Check("Part 3: Pauli-frame enumeration reproduces both closed forms", gap < 1e-12,
              f"max difference {gap:.1e}"),
    ]
    print("\n(e) Sweep under phase-flip noise. Each code is probed with the state its")
    print("    failures disturb: |+> for the bare qubit and the bit-flip code,")
    print("    |0> for the phase-flip code.\n")
    print_error_rate_sweep([("bare", sweeps["bare"], lambda p: p),
                            ("bit-flip code", sweeps["bit_flip"], odd_flip_closed_form),
                            ("phase code", sweeps["phase_flip"], rep3_closed_form)])

    depolarizing = exact_logical_error_curve(REP3_CODE, build_decoder(REP3_CODE), "depolarizing")
    always_worse = all(depolarizing(float(p)) > p for p in P_SWEEP_EXACT[1:])
    checks.append(Check("Part 3: bit-flip code never breaks even under depolarizing noise",
                        always_worse and break_even_point(depolarizing) is None,
                        "logical > physical at every p in (0, 0.5]; at p = 0.01: "
                        f"{depolarizing(0.01):.4f}"))
    print("\n  KEY RESULT: against phase flips the bit-flip code is WORSE than no code (about 3p).")
    print("  Under depolarizing noise (X, Y, Z equally likely) it never breaks even at any p:")
    print(f"  at p = 0.01 its logical error is {depolarizing(0.01):.4f}, about double the bare")
    print("  qubit. A useful code must handle both error types at once.")
    return depolarizing, checks


# ======================================================================================
# PART 4 -- SHOR'S 9-QUBIT CODE: ANY SINGLE-QUBIT ERROR
# ======================================================================================
# Peter Shor, 1995: the first code to protect a qubit against ARBITRARY errors.
#
# CONCATENATION: A CODE MADE OF CODES
#   Outer layer: the phase-flip code from Part 3, protecting one qubit with three.
#   Inner layer: each of those three is itself a bit-flip block from Part 2.
#
#       |0_L> = (|000> + |111>)(|000> + |111>)(|000> + |111>) / 2^(3/2)
#       |1_L> = (|000> - |111>)(|000> - |111>)(|000> - |111>) / 2^(3/2)
#
#   * A bit flip inside a block breaks that block's pair agreement: 6 Z-type checks
#     (Z0Z1, Z1Z2 per block) find it, exactly as in Part 2.
#   * A phase flip anywhere in a block flips that BLOCK's sign (+ becomes -). Two X-type
#     checks compare signs block against block (X0...X5 and X3...X8), the Part 3 idea
#     one level up.
#   * Y = iXZ is both at once, so it trips one check of each kind and gets both fixes.
#
# WHY FIXING X, Y AND Z IS ENOUGH FOR *ANY* ERROR
#   Real errors are continuous: a slight over-rotation, a partial loss of phase. But any
#   single-qubit error E can be written E = c0*I + c1*X + c2*Y + c3*Z. Measuring the
#   syndrome forces the state to pick ONE of those terms (it is a measurement, and the
#   four terms give different syndrome answers), and the correction then undoes that
#   term exactly. Continuous errors are digitized by the act of checking for them. Part 4
#   verifies this below with a random non-Pauli rotation.
#
# HOW TO SCORE ARBITRARY ERRORS FAIRLY: A PERFECT REFERENCE QUBIT
#   Testing with |0> misses Z errors (Z|0> = |0>); testing with |+> misses X errors. So
#   the logical qubit is first entangled with a reference qubit that never touches the
#   noise, forming the Bell pair (|0>|0> + |1>|1>) / sqrt(2). Each logical error leaves
#   its own fingerprint on that pair, and undoing the Bell pair turns the fingerprint into
#   two bits: data 0 reads 1 for a logical Z, the reference reads 1 for a logical X, both
#   for a logical Y. The chance of reading all zeros is the probability that the logical
#   error was the identity (one minus the entanglement infidelity): one number that covers
#   every input state at once. A bare qubit under the same noise scores exactly 1 - p, so
#   y = p is still the right break-even line.

SHOR_BLOCKS = ((0, 1, 2), (3, 4, 5), (6, 7, 8))
SHOR_CODE = make_css_code(
    "Shor 9-qubit code", 9,
    x_checks=[range(0, 6), range(3, 9)],
    z_checks=[(0, 1), (1, 2), (3, 4), (4, 5), (6, 7), (7, 8)],
    logical_x=([], range(9)),        # Z on all nine flips every block sign: |0_L> <-> |1_L>
    logical_z=(range(9), []),        # X on all nine: sign (-1)^3 on |1_L> only
)
LOGICAL_ERROR_NAME = {(0, 0): "none", (1, 0): "X", (0, 1): "Z", (1, 1): "Y"}   # (x, z) flags


def encode_shor(qc: QuantumCircuit, data: Qubits) -> None:
    qc.cx(data[0], data[3])                          # outer layer: phase-flip code...
    qc.cx(data[0], data[6])
    for leader in (0, 3, 6):
        qc.h(data[leader])                           # ...in the Hadamard basis
    for block in SHOR_BLOCKS:                        # inner layer: bit-flip code per block
        encode_repetition(qc, [data[q] for q in block])


def decode_shor(qc: QuantumCircuit, data: Qubits) -> None:
    for block in SHOR_BLOCKS:
        decode_repetition(qc, [data[q] for q in block])
    for leader in (0, 3, 6):
        qc.h(data[leader])
    qc.cx(data[0], data[6])
    qc.cx(data[0], data[3])


def measure_shor_syndrome(qc: QuantumCircuit, data: Qubits, bit_anc: Qubits,
                          phase_anc: Qubits, block_regs: Sequence[ClassicalRegister],
                          phase_reg: ClassicalRegister) -> None:
    """Six bit-flip parities (Part 2, per block) and two block-sign comparisons.

    Measuring X0...X5 with an ancilla: prepare it in |+>, let it control X on each of the
    six data qubits, rotate back with H. If the data is a +1 eigenstate of X0...X5 the
    ancilla returns to |0>; if a phase flip made it -1, the ancilla picks up that sign
    (phase kickback) and reads 1. The data itself is never measured.
    """
    for b, block in enumerate(SHOR_BLOCKS):
        measure_bit_parities(qc, [data[q] for q in block], bit_anc[2 * b:2 * b + 2],
                             block_regs[b])
    for k, span in enumerate((range(0, 6), range(3, 9))):
        qc.h(phase_anc[k])
        for q in span:
            qc.cx(phase_anc[k], data[q])
        qc.h(phase_anc[k])
        qc.measure(phase_anc[k], phase_reg[k])


def correct_shor(qc: QuantumCircuit, data: Qubits, block_regs: Sequence[ClassicalRegister],
                 phase_reg: ClassicalRegister) -> None:
    """Part 2's table inside each block, then Part 3's table across blocks.

    A phase flip anywhere in a block is equivalent to one on any other qubit of that block
    (they differ by a stabilizer), so Z on the block's first qubit fixes all three cases.
    """
    for b, block in enumerate(SHOR_BLOCKS):
        correct_bit_flip(qc, [data[q] for q in block], block_regs[b])
    for value, b in REP3_CORRECTION.items():
        with qc.if_test((phase_reg, value)):
            qc.z(data[SHOR_BLOCKS[b][0]])


def entangle_with_reference(qc: QuantumCircuit, logical: Qubit, reference: Qubit) -> None:
    qc.h(logical)
    qc.cx(logical, reference)


def unentangle_from_reference(qc: QuantumCircuit, logical: Qubit, reference: Qubit) -> None:
    qc.cx(logical, reference)
    qc.h(logical)


def shor_memory_circuit(add_errors: Callable[[QuantumCircuit, Qubits], None]) -> QuantumCircuit:
    """Reference Bell pair -> encode -> errors -> syndrome -> correct -> decode -> score."""
    data, ref = QuantumRegister(9, "data"), QuantumRegister(1, "ref")
    bit_anc, phase_anc = QuantumRegister(6, "bit_anc"), QuantumRegister(2, "phase_anc")
    block_regs = [ClassicalRegister(2, f"block{b}") for b in range(3)]
    phase_reg, readout = ClassicalRegister(2, "phase"), ClassicalRegister(10, "readout")
    qc = QuantumCircuit(data, bit_anc, phase_anc, ref, *block_regs, phase_reg, readout)
    entangle_with_reference(qc, data[0], ref[0])
    encode_shor(qc, data)
    add_errors(qc, data)
    measure_shor_syndrome(qc, data, bit_anc, phase_anc, block_regs, phase_reg)
    correct_shor(qc, data, block_regs, phase_reg)
    decode_shor(qc, data)
    unentangle_from_reference(qc, data[0], ref[0])
    qc.measure(list(data) + [ref[0]], readout)        # readout bit 0 = data 0, bit 9 = ref
    return qc


def shor_logical_error(registers: dict[str, int]) -> tuple[str, bool]:
    """(logical error name, True if the other 8 data qubits returned to |0> as they must)."""
    readout = registers["readout"]
    flags = ((readout >> 9) & 1, readout & 1)
    return LOGICAL_ERROR_NAME[flags], (readout & 0b0111111110) == 0


def deterministic_registers(qc: QuantumCircuit) -> dict[str, int]:
    """Run a circuit whose outcome should not be random, and insist that it is not."""
    counts = run_counts(qc, 32, SEED)
    if len(counts) != 1:
        raise RuntimeError(f"expected one deterministic outcome, got {counts}")
    return register_values(next(iter(counts)), qc)


def shor_error_demo_cell(errors: dict[int, str]) -> tuple[str, str, bool]:
    qc = shor_memory_circuit(lambda c, data: inject_paulis(c, data, errors))
    reg = deterministic_registers(qc)
    blocks = ".".join(parity_pair(reg[f"block{b}"]) for b in range(3))
    logical, in_code_space = shor_logical_error(reg)
    return f"{blocks} {parity_pair(reg['phase'])} -> {'ok' if logical == 'none' else logical}", \
        logical, in_code_space


def shor_continuous_error_demo() -> Check:
    """A random NON-Pauli rotation on one qubit: the syndrome digitizes it, then it is fixed."""
    def add_rotation(qc: QuantumCircuit, data: Qubits) -> None:
        qc.barrier(label="error")
        qc.u(0.9, 0.4, -1.3, data[4])                 # an arbitrary single-qubit unitary
        qc.barrier()

    qc = shor_memory_circuit(add_rotation)
    counts = run_counts(qc, 24, SEED, simulator=STATEVECTOR_SIM)   # not Clifford: statevector
    outcomes = [register_values(key, qc) for key in counts]
    syndromes = {(o["block1"], o["phase"]) for o in outcomes}
    all_clean = all(shor_logical_error(o) == ("none", True) for o in outcomes)
    print(f"\n(b) A continuous error: U(0.9, 0.4, -1.3) on data 4, 24 shots (statevector method).")
    print(f"    Syndromes seen (block 1, phase): "
          f"{sorted((parity_pair(b), parity_pair(ph)) for b, ph in syndromes)}")
    print(f"    Logical error in every shot: {'none' if all_clean else 'SOME SHOTS FAILED'}")
    print("    Different shots collapsed the rotation onto different Paulis (different")
    print("    syndromes), and each got the matching fix. That is error digitization.")
    return Check("Part 4: arbitrary rotation digitized and corrected in every shot",
                 all_clean and len(syndromes) > 1, f"{len(syndromes)} distinct syndromes")


def part4_shor_code() -> tuple[Sweep, Callable[[float], float], list[Check]]:
    section("PART 4 -- SHOR'S 9-QUBIT CODE: correcting X, Y and Z")
    checks = validate_code(SHOR_CODE, expected_distance=3)
    sample = shor_memory_circuit(lambda c, data: None)
    print(f"Circuit: {sample.num_qubits} qubits (9 data, 6 bit-flip ancillas, 2 phase ancillas,")
    print(f"1 reference), {sample.count_ops().get('cx', 0)} CNOTs. Each cell below shows")
    print("block syndromes (d0^d1 d1^d2 per block) | block-sign syndrome -> logical outcome.\n")
    print(f"  {'':>7} {'X error':>24}  {'Y error':>24}  {'Z error':>24}")
    all_fixed = True
    for q in range(9):
        cells = [shor_error_demo_cell({q: letter}) for letter in "XYZ"]
        all_fixed &= all(logical == "none" and clean for _, logical, clean in cells)
        print(f"  data {q}  " + "  ".join(f"{text:>24}" for text, _, _ in cells))
    checks.append(Check("Part 4: all 27 single-qubit X/Y/Z errors corrected", all_fixed))

    print("\n    The limit: distance 3 means two well-placed errors defeat it.")
    for errors, expected, why in (
            ({0: "X", 1: "X"}, "Z", "two flips in one block: majority vote picks the wrong fix"),
            ({0: "Z", 3: "Z"}, "X", "phase flips in two blocks: block vote picks the wrong fix")):
        text, logical, _ = shor_error_demo_cell(errors)
        label = " + ".join(f"{p} on data {q}" for q, p in errors.items())
        print(f"    {label:<26} {text:<16} ({why})")
        checks.append(Check(f"Part 4: {label} causes logical {expected}", logical == expected,
                            f"got {logical}"))
    checks.append(shor_continuous_error_demo())

    sweep = run_sweep("Shor 9-qubit", lambda p: shor_memory_circuit(
        lambda c, data: inject_noise(c, data, depolarizing_channel(p))),
        lambda reg: reg["readout"] != 0, seed_tag=400)
    exact = exact_logical_error_curve(SHOR_CODE, build_decoder(SHOR_CODE), "depolarizing")
    checks.append(agreement_check("Part 4: Shor circuit sweep matches exact enumeration",
                                  sweep, exact))
    print("\n(c) Sweep under depolarizing noise (X, Y, Z each p/3 on every data qubit).")
    print("    exact = all 4^9 = 262,144 error patterns enumerated and decoded.\n")
    print_error_rate_sweep([("Shor-9", sweep, exact)])
    p_star = break_even_point(exact)
    print(f"\n  KEY RESULT: break-even at p* = {percent(p_star)} under depolarizing noise.")
    print("  Below it, 9 physical qubits make a better qubit than 1. The first code ever to do")
    print("  that against every kind of single-qubit error, not just one kind.")
    return sweep, exact, checks


# ======================================================================================
# PART 5 -- THE SURFACE CODE
# ======================================================================================
# WHY THE WHOLE INDUSTRY STARTED HERE
#   Shor's code needs checks like X0X1X2X3X4X5: one ancilla wired to six data qubits spread
#   across the chip. Hardware qubits mostly talk to their immediate neighbours. The surface
#   code (Kitaev 1997; Bravyi & Kitaev 1998; Dennis et al. 2002) is arranged so that EVERY
#   check touches at most four qubits that are adjacent on a flat grid. Three properties
#   made it the default roadmap:
#     1. LOCAL CONNECTIVITY. Nearest-neighbour couplings only; it fits a planar chip.
#     2. HIGH THRESHOLD. About 1% under realistic circuit-level noise, the most forgiving
#        of the practical codes, and today's best hardware is already below it.
#     3. SCALABILITY. Distance d is just the grid size. Growing d changes nothing about
#        any individual check, so the same hardware cell tiles out to any size.
#   Google's Willow chip (Nature, December 2024; arXiv:2408.13687) ran distance 3, 5 and 7 surface
#   codes and saw the logical error rate fall by about 2.14x per step of 2 in distance:
#   the first clean experimental signature of operating below threshold.
#
# THE DISTANCE-3 "ROTATED" LAYOUT USED HERE (9 data qubits, 8 checks, 17 qubits total)
#
#                       x             data qubits sit on grid points 0..8
#          0 ------ 1 ------ 2        x = X-type check (X on its corners), detects Z errors
#     z    |    x   |    z   |        z = Z-type check (Z on its corners), detects X errors
#          3 ------ 4 ------ 5
#          |    z   |    x   |   z    Bulk checks are weight 4, boundary checks weight 2.
#          6 ------ 7 ------ 8        X-type boundaries top and bottom, Z-type left/right.
#              x
#
#   Logical Z = Z0 Z1 Z2 (a row, running between the left and right Z-type boundaries).
#   Logical X = X0 X3 X6 (a column, running between the top and bottom X-type boundaries).
#   The shortest chain of errors that crosses the patch without lighting any check is
#   3 long. That is the distance, and it grows with the patch.
#
# SYNDROMES ARE A PICTURE
#   An X error on a data qubit lights up the Z checks on either side of it. A CHAIN of X
#   errors lights only its two END points: the checks in the middle see two flips and stay
#   dark. Decoding is therefore a geometry problem: given the lit checks, guess the
#   shortest chains that could connect them (or connect them to a boundary). Production
#   decoders solve this with minimum-weight perfect matching. At distance 3 there are only
#   16 possible syndromes per check type, so an exact minimum-weight lookup table does the
#   same job, built by the breadth-first search in Section 0b.
#
# CNOT ORDER MATTERS ON HARDWARE (a "hook" error)
#   If an ancilla fails halfway through its four CNOTs, the fault copies onto the two data
#   qubits it has not reached yet. X checks visit NW, NE, SW, SE (a "Z" shape) and Z checks
#   visit NW, SW, NE, SE (an "N" shape) so that those two-qubit hooks lie PERPENDICULAR to
#   the logical operator they could otherwise help complete. Syndrome extraction here is
#   perfect, so order changes no number below; the standard order is used anyway.

@dataclass(frozen=True)
class Plaquette:
    """One stabilizer check drawn as a face of the grid."""

    kind: str                   # "X" or "Z"
    face: tuple[int, int]       # (i, j): corners are data (i,j), (i,j+1), (i+1,j), (i+1,j+1)
    qubits: tuple[int, ...]     # data qubit indices in hardware CNOT order


@dataclass(frozen=True)
class SurfaceLayout:
    d: int
    x_plaquettes: tuple[Plaquette, ...]
    z_plaquettes: tuple[Plaquette, ...]
    code: CssCode


def rotated_surface_code(d: int) -> SurfaceLayout:
    """Build the distance-d rotated surface code by checkerboard-colouring grid faces.

    Face (i, j) is X-type when i + j is even. Every interior face becomes a weight-4 check.
    Half-faces on the boundary become weight-2 checks, X-type only along the top and
    bottom edges and Z-type only along the left and right edges, which is exactly what
    leaves one logical qubit behind.
    """
    def index(r: int, c: int) -> int:
        return r * d + c

    x_plaquettes, z_plaquettes = [], []
    for i in range(-1, d):
        for j in range(-1, d):
            corners = [(r, c) for r in (i, i + 1) for c in (j, j + 1)
                       if 0 <= r < d and 0 <= c < d]            # NW, NE, SW, SE order
            kind = "X" if (i + j) % 2 == 0 else "Z"
            on_top_or_bottom = i in (-1, d - 1)
            if not (len(corners) == 4 or (len(corners) == 2 and on_top_or_bottom == (kind == "X"))):
                continue
            qubits = [index(r, c) for r, c in corners]
            if kind == "Z" and len(qubits) == 4:
                qubits = [qubits[0], qubits[2], qubits[1], qubits[3]]    # N shape
            target = x_plaquettes if kind == "X" else z_plaquettes
            target.append(Plaquette(kind, (i, j), tuple(qubits)))
    code = make_css_code(f"surface code d={d}", d * d,
                         x_checks=[p.qubits for p in x_plaquettes],
                         z_checks=[p.qubits for p in z_plaquettes],
                         logical_x=([index(r, 0) for r in range(d)], []),
                         logical_z=([], [index(0, c) for c in range(d)]))
    return SurfaceLayout(d, tuple(x_plaquettes), tuple(z_plaquettes), code)


def render_lattice(layout: SurfaceLayout, errors: dict[int, str],
                   fired: set[tuple[str, int]]) -> list[str]:
    """ASCII picture: digits = data qubits, (X) = injected error, x/z = quiet check,
    [X]/[Z] = check that fired. Grid cell (2r+1, 2c+1) holds data (r, c); face (i, j)
    sits between its four corners at (2i+2, 2j+2)."""
    d = layout.d
    grid = [["" for _ in range(2 * d + 1)] for _ in range(2 * d + 1)]
    for q in range(d * d):
        r, c = divmod(q, d)
        grid[2 * r + 1][2 * c + 1] = f"({errors[q]})" if q in errors else str(q)
    for plaquettes in (layout.x_plaquettes, layout.z_plaquettes):
        for k, plaq in enumerate(plaquettes):
            i, j = plaq.face
            lit = (plaq.kind, k) in fired
            grid[2 * i + 2][2 * j + 2] = f"[{plaq.kind}]" if lit else plaq.kind.lower()
    return ["".join(f"{cell:^4}" for cell in row).rstrip() for row in grid]


def fired_checks(registers: dict[str, int], layout: SurfaceLayout) -> set[tuple[str, int]]:
    fired = {("X", k) for k in range(len(layout.x_plaquettes)) if registers["x_checks"] >> k & 1}
    return fired | {("Z", k) for k in range(len(layout.z_plaquettes))
                    if registers["z_checks"] >> k & 1}


def logical_bell_generators(code: CssCode) -> list[Pauli]:
    """The n + 1 stabilizers of the logical Bell pair between the code and a reference qubit:
    every code check, plus (logical X)(X_ref) and (logical Z)(Z_ref).

    GOTCHA: Pauli((z, x)) takes the Z bits FIRST, and bit k is qubit k (little-endian).
    """
    empty = np.zeros(code.n + 1, dtype=bool)

    def padded(bits: np.ndarray, ref_bit: int) -> np.ndarray:
        return np.append(bits, ref_bit).astype(bool)

    def pauli(x_bits: np.ndarray, z_bits: np.ndarray) -> Pauli:
        return Pauli((z_bits, x_bits))

    return ([pauli(padded(row, 0), empty) for row in code.x_checks]
            + [pauli(empty, padded(row, 0)) for row in code.z_checks]
            + [pauli(padded(code.logical_x[0], 1), padded(code.logical_x[1], 0)),
               pauli(padded(code.logical_z[0], 0), padded(code.logical_z[1], 1))])


def logical_bell_prep(code: CssCode) -> QuantumCircuit:
    """Synthesize a circuit that prepares the logical Bell pair from |0...0>.

    Those n + 1 commuting stabilizers pin down a unique state, and qiskit.quantum_info turns
    the list into a Clifford circuit. It uses long-range gates, which is fine: like the
    reference qubit, it is an ideal scoring device, not part of the noisy experiment.
    """
    generators = logical_bell_generators(code)
    return StabilizerState.from_stabilizer_list(generators).clifford.to_circuit()


def prepares_all_stabilizers(circuit: QuantumCircuit, generators: Sequence[Pauli]) -> bool:
    state = StabilizerState(circuit)
    return all(state.expectation_value(g) == 1 for g in generators)


def measure_surface_syndrome(qc: QuantumCircuit, layout: SurfaceLayout, data: Qubits,
                             x_anc: Qubits, z_anc: Qubits,
                             x_reg: ClassicalRegister, z_reg: ClassicalRegister) -> None:
    """One round of all 8 checks. Hardware interleaves them into 4 parallel CNOT layers;
    with perfect extraction, running them one after another is equivalent."""
    for k, plaq in enumerate(layout.z_plaquettes):
        for q in plaq.qubits:
            qc.cx(data[q], z_anc[k])                  # ancilla collects the Z parity
        qc.measure(z_anc[k], z_reg[k])
    for k, plaq in enumerate(layout.x_plaquettes):
        qc.h(x_anc[k])
        for q in plaq.qubits:
            qc.cx(x_anc[k], data[q])                  # phase kickback collects the X parity
        qc.h(x_anc[k])
        qc.measure(x_anc[k], x_reg[k])


def apply_lookup_correction(qc: QuantumCircuit, data: Qubits, syndrome: ClassicalRegister,
                            table: np.ndarray, gate: str) -> None:
    for value in range(1, table.shape[0]):
        with qc.if_test((syndrome, value)):
            for q in np.flatnonzero(table[value]):
                getattr(qc, gate)(data[int(q)])


def surface_memory_circuit(layout: SurfaceLayout, prep: QuantumCircuit, decoder: LookupDecoder,
                           add_errors: Callable[[QuantumCircuit, Qubits], None]) -> QuantumCircuit:
    n = layout.code.n
    data, ref = QuantumRegister(n, "data"), QuantumRegister(1, "ref")
    x_anc = QuantumRegister(len(layout.x_plaquettes), "x_anc")
    z_anc = QuantumRegister(len(layout.z_plaquettes), "z_anc")
    x_reg = ClassicalRegister(len(layout.x_plaquettes), "x_checks")
    z_reg = ClassicalRegister(len(layout.z_plaquettes), "z_checks")
    readout = ClassicalRegister(n + 1, "readout")
    qc = QuantumCircuit(data, x_anc, z_anc, ref, x_reg, z_reg, readout)
    scored = list(data) + [ref[0]]
    qc.compose(prep, scored, inplace=True)
    add_errors(qc, data)
    measure_surface_syndrome(qc, layout, data, x_anc, z_anc, x_reg, z_reg)
    apply_lookup_correction(qc, data, z_reg, decoder.x_correction, "x")   # Z checks -> X fixes
    apply_lookup_correction(qc, data, x_reg, decoder.z_correction, "z")   # X checks -> Z fixes
    qc.compose(prep.inverse(), scored, inplace=True)
    qc.measure(scored, readout)          # all zeros <=> logical qubit and reference intact
    return qc


def print_plaquette_circuits() -> None:
    """Show what 'measure a stabilizer' means in gates, for one check of each type."""
    for kind in ("Z", "X"):
        qc = QuantumCircuit(QuantumRegister(4, "data"), QuantumRegister(1, "anc"),
                            ClassicalRegister(1, "s"))
        if kind == "X":
            qc.h(4)
        for q in range(4):
            if kind == "Z":
                qc.cx(q, 4)
            else:
                qc.cx(4, q)
        if kind == "X":
            qc.h(4)
        qc.measure(4, 0)
        print(f"  {kind}-type check (measures {kind}{kind}{kind}{kind} on its four corners):")
        print(qc.draw(output="text", fold=-1))


def surface_error_demo(layout: SurfaceLayout, prep: QuantumCircuit, decoder: LookupDecoder,
                       errors: dict[int, str]) -> tuple[list[str], int]:
    """Inject specific errors; return the lattice picture and the scoring readout (0 = ok)."""
    qc = surface_memory_circuit(layout, prep, decoder,
                                lambda c, data: inject_paulis(c, data, errors))
    registers = deterministic_registers(qc)
    picture = render_lattice(layout, errors, fired_checks(registers, layout))
    return picture, registers["readout"]


def print_side_by_side(titles: Sequence[str], pictures: Sequence[list[str]],
                       width: int = 30) -> None:
    print("  " + "".join(f"{t:<{width}}" for t in titles))
    for rows in zip(*pictures):
        print("  " + "".join(f"{row:<{width}}" for row in rows))


def part5_surface_code() -> tuple[Sweep, Callable[[float], float], tuple[float, ...], list[Check]]:
    section("PART 5 -- THE SURFACE CODE: distance-3 rotated patch (17 physical qubits)")
    layout3, layout5 = rotated_surface_code(3), rotated_surface_code(5)
    checks = validate_code(layout3.code, expected_distance=3)
    checks += validate_code(layout5.code, expected_distance=5)
    prep, decoder = logical_bell_prep(layout3.code), build_decoder(layout3.code)
    generators = logical_bell_generators(layout3.code)
    empty_circuit = QuantumCircuit(layout3.code.n + 1)
    checks.append(Check("Part 5: synthesized Bell-pair prep satisfies all 10 stabilizers",
                        prepares_all_stabilizers(prep, generators)
                        and not prepares_all_stabilizers(empty_circuit, generators),
                        "and the same test rejects an empty circuit"))

    print("Checks and the order their CNOTs visit the data qubits:")
    for plaq in layout3.x_plaquettes + layout3.z_plaquettes:
        print(f"  {plaq.kind}-check on face {str(plaq.face):>8}: data {plaq.qubits}")
    print()
    print_plaquette_circuits()

    print("\n(a) Syndromes on the lattice, read from the simulated ancilla measurements.")
    print("    digits = data qubits, (X) = injected error, x/z = quiet check, [X]/[Z] = fired\n")
    demos = [({4: "X"}, "X on centre qubit 4"), ({0: "Z"}, "Z on corner qubit 0"),
             ({4: "Y"}, "Y on qubit 4 (X and Z)")]
    results = [surface_error_demo(layout3, prep, decoder, errors) for errors, _ in demos]
    print_side_by_side([f"{title} -> {'ok' if readout == 0 else 'FAIL'}"
                        for (_, title), (_, readout) in zip(demos, results)],
                       [picture for picture, _ in results])
    print("\n    X lights neighbouring Z checks, Z lights neighbouring X checks, Y lights both.")

    singles = [surface_error_demo(layout3, prep, decoder, {q: letter})[1] == 0
               for q in range(9) for letter in "XYZ"]
    print(f"\n(b) All 27 single-qubit errors (X, Y, Z on each data qubit): "
          f"{sum(singles)}/27 corrected.")
    checks.append(Check("Part 5: all 27 single-qubit X/Y/Z errors corrected", all(singles)))

    picture, readout = surface_error_demo(layout3, prep, decoder, {0: "X", 3: "X"})
    _, logical_x_readout = surface_error_demo(layout3, prep, decoder, {0: "X", 3: "X", 6: "X"})
    print("\n(c) The limit: X on qubits 0 and 3. They cancel on the boundary check Z0Z3, which")
    print("    stays dark, and light only the check below them. The decoder blames qubit 6,")
    print("    and the residual X0 X3 X6 is exactly logical X.\n")
    print_side_by_side([f"X on 0 and 3 -> {'ok' if readout == 0 else 'logical error'}"],
                       [picture])
    checks.append(Check("Part 5: a length-2 chain plus the decoder's guess makes logical X",
                        readout != 0 and readout == logical_x_readout,
                        f"readout {readout:010b} matches injected X0 X3 X6 "
                        f"{logical_x_readout:010b}"))

    sweep = run_sweep("surface d=3", lambda p: surface_memory_circuit(
        layout3, prep, decoder, lambda c, data: inject_noise(c, data, depolarizing_channel(p))),
        lambda reg: reg["readout"] != 0, seed_tag=500)
    exact = exact_logical_error_curve(layout3.code, decoder, "depolarizing")
    checks.append(agreement_check("Part 5: surface d=3 circuit sweep matches exact enumeration",
                                  sweep, exact))
    print("\n(d) Sweep under depolarizing noise, full 18-qubit circuits vs exact enumeration.\n")
    print_error_rate_sweep([("surface d=3", sweep, exact)])
    print(f"\n  Break-even at p* = {percent(break_even_point(exact))} under code-capacity "
          "depolarizing noise.")

    d5_rates, frame_check = distance5_by_pauli_frames(layout3, exact, layout5)
    checks.append(frame_check)
    return sweep, exact, d5_rates, checks


def distance5_by_pauli_frames(layout3: SurfaceLayout, exact3: Callable[[float], float],
                              layout5: SurfaceLayout) -> tuple[tuple[float, ...], Check]:
    """Distance 5 = 49 qubits. Sample Pauli frames instead of simulating circuits.

    The distance-3 comparison below checks the random SAMPLING step only: the decoder and
    the failure test are shared with the exact enumeration, which was validated against
    the Qiskit circuits above. The d=5 code definition and decoder rest on the structural
    checks (commutation, one logical qubit, distance 5).
    """
    decoder3, decoder5 = build_decoder(layout3.code), build_decoder(layout5.code)
    trial_ps = (0.05, 0.10, 0.20)
    scores = [z_score(sampled_logical_error_rate(layout3.code, decoder3, p, SURFACE_D5_SAMPLES,
                                                 np.random.default_rng([SEED, 53, k])),
                      exact3(p), SURFACE_D5_SAMPLES) for k, p in enumerate(trial_ps)]
    check = Check("Part 5: Pauli-frame sampler reproduces exact d=3 curve",
                  max(scores) <= Z_SCORE_LIMIT,
                  f"worst {max(scores):.2f} standard errors at p = {trial_ps}")
    rates = tuple(sampled_logical_error_rate(layout5.code, decoder5, float(p), SURFACE_D5_SAMPLES,
                                             np.random.default_rng([SEED, 55, k]))
                  for k, p in enumerate(P_SWEEP_LOG))
    print(f"\n(e) Distance 5 (25 data + 24 ancilla = 49 qubits), "
          f"{SURFACE_D5_SAMPLES:,} Pauli-frame samples per point.")
    print(f"    The sampler first matched the exact d=3 curve (worst {max(scores):.2f} "
          "standard errors).\n")
    print(f"  {'p':>6}   {'bare':>7}   {'d=3 exact':>9}   {'d=5 sampled':>11}")
    for p, r5 in zip(P_SWEEP_LOG, rates):
        print(f"  {p:>6.3f}   {p:>7.4f}   {exact3(p):>9.5f}   {r5:>11.5f}")
    return rates, check


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
#   This chart is code-capacity noise (perfect checks). Real syndrome extraction uses
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
#   With the usual rule of thumb P_L ~ 0.1 * (p / 1%)^((d+1)/2), hardware at p = 0.1%
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
                    f"surface d=3 at {percent(break_even['surface3'], 1)}",
                    xy=(break_even["surface3"], break_even["surface3"]), xytext=(0.02, 0.40),
                    fontsize=9, color=INK,
                    arrowprops={"arrowstyle": "-", "color": INK_MUTED, "lw": 0.8})
    ax.annotate("3-qubit code (bit flips only)\nbreaks even at exactly 50%",
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
        ax.text(threshold * 0.92, 2.5e-4, f"threshold\n~{threshold:.1%}", ha="right",
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
    print("\nThreshold estimate, where the d=3 and d=5 curves cross: "
          f"{percent(analysis.threshold, 1, 'not found')}")
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


# ======================================================================================
# SUMMARY AND SELF-VERIFICATION
# ======================================================================================

def print_summary(analysis: ThresholdAnalysis, phase_break_even: float | None) -> None:
    section("SUMMARY")
    be = analysis.break_even
    rows = (
        ("Unencoded qubit", "1", "nothing", "--"),
        ("3-qubit bit-flip", "3 data + 2 ancilla = 5", "any 1 bit flip (X)",
         f"{percent(be['rep3_bit_flip'], 0)} bit-flip noise; "
         f"{percent(be['rep3_depolarizing'])} under depolarizing"),
        ("3-qubit phase-flip", "3 data + 2 ancilla = 5", "any 1 phase flip (Z)",
         f"{percent(phase_break_even, 0)} phase-flip noise"),
        ("Shor 9-qubit", "9 data + 8 ancilla = 17", "any 1-qubit error",
         f"{percent(be['shor'])} depolarizing"),
        ("Surface d=3", "9 data + 8 ancilla = 17", "any 1-qubit error",
         f"{percent(be['surface3'])} depolarizing"),
        ("Surface d=5", "25 data + 24 ancilla = 49", "any 2-qubit error",
         f"~{percent(analysis.d5_break_even, 1)} depolarizing (sampled)"),
    )
    print(f"  {'code':<20} {'physical qubits':<27} {'corrects':<22} "
          "break-even (code-capacity noise)")
    for row in rows:
        print(f"  {row[0]:<20} {row[1]:<27} {row[2]:<22} {row[3]}")
    print("\n  Surface-code threshold (d=3 vs d=5 crossing): "
          f"~{percent(analysis.threshold, 1, 'not found')} code capacity;")
    print("  about 1% under realistic circuit-level noise. One noiseless reference qubit is added")
    print("  for scoring in Parts 4-5 and is not part of any code.")


def print_verification(checks: Sequence[Check]) -> bool:
    section("SELF-VERIFICATION")
    for check in checks:
        detail = f"  ({check.detail})" if check.detail else ""
        print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}{detail}")
    passed = sum(check.passed for check in checks)
    print(f"\n  {passed}/{len(checks)} checks passed.")
    return passed == len(checks)


def main() -> int:
    # GOTCHA (Windows): when output is piped, Python uses cp1252 and Qiskit's circuit
    # drawer (box-drawing characters) cannot print. Force UTF-8 for this process.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 88)
    print("QUANTUM ERROR CORRECTION -- from one noisy qubit to the surface code")
    print(f"Run {datetime.now().isoformat(timespec='seconds')} | qiskit {qiskit.__version__} | "
          f"qiskit-aer {qiskit_aer.__version__} | numpy {np.__version__} | seed {SEED}")
    print("=" * 88)

    _, checks = part1_bare_qubit()
    rep3_sweep, rep3_curve, more = part2_bit_flip_code()
    checks += more
    rep3_depolarizing, more = part3_phase_flips()
    checks += more
    shor_sweep, shor_curve, more = part4_shor_code()
    checks += more
    surface_sweep, surface_curve, d5_rates, more = part5_surface_code()
    checks += more

    curves = {"rep3_bit_flip": rep3_curve, "rep3_depolarizing": rep3_depolarizing,
              "shor": shor_curve, "surface3": surface_curve}
    sweeps = {"rep3_bit_flip": rep3_sweep, "shor": shor_sweep, "surface3": surface_sweep}
    analysis, more = part6_threshold_chart(curves, sweeps, d5_rates)
    checks += more
    phase_curve = exact_logical_error_curve(PHASE_CODE, build_decoder(PHASE_CODE), "phase_flip")
    print_summary(analysis, break_even_point(phase_curve))
    return 0 if print_verification(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
