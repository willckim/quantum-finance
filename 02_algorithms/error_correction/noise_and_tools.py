"""Shared settings, simulators, noise channels and result-reading helpers.

Every other module imports from here. Nothing in this file belongs to one code: it
holds the run-wide configuration (seed, shots, sweep grids), the Aer simulators, the
Pauli noise channels, and the small tools that run circuits, parse their results and
grade them against exact values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import Qubit
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

# Part 7 (biased noise). Change these to rerun the comparison at other settings.
BIAS_RATIOS = (1, 10, 100, 1000)   # eta = P(Z) / P(X), ascending from 1; circuits run at each
P_BIAS = 0.05                      # total physical error rate held fixed while eta varies
REP_LENGTHS = (3, 5, 7)            # phase-flip repetition codes: odd, 3 to 9 (exact enumeration)
BIAS_PNG = Path(__file__).resolve().with_name("biased_noise.png")

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


# BIASED NOISE (Part 7). The total error probability stays p; the bias ratio eta decides
# how it splits. eta = P(Z) / P(X), with P(Y) = P(X) because Y = iXZ also flips the bit:
#     P(X) = P(Y) = p / (eta + 2),   P(Z) = p * eta / (eta + 2)
# eta = 1 is exactly the depolarizing channel above (p/3 each); eta = 1000 means a phase
# flip is 1000 times as likely as a bit flip. GOTCHA when reading papers: Tuckett et al.
# (PRL 2018) define eta = P(Z) / (P(X) + P(Y)), which is HALF of this script's eta, so
# their "depolarizing" is eta = 0.5 and their eta = 500 is this script's 1000.
def bias_split(p: float, eta: float) -> tuple[float, float, float]:
    """(P(X), P(Y), P(Z)) for total error rate p and bias ratio eta = P(Z) / P(X)."""
    if eta <= 0 or not 0 <= p <= 1:
        raise ValueError(f"need eta > 0 and 0 <= p <= 1, got eta={eta}, p={p}")
    bit = p / (eta + 2)
    return bit, bit, p - 2 * bit


def biased_channel(p: float, eta: float):
    """Aer Pauli channel: X, Y, Z with the probabilities bias_split gives, total p."""
    p_x, p_y, p_z = bias_split(p, eta)
    return pauli_error([("X", p_x), ("Y", p_y), ("Z", p_z), ("I", 1.0 - p)])


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


def deterministic_registers(qc: QuantumCircuit) -> dict[str, int]:
    """Run a circuit whose outcome should not be random, and insist that it is not."""
    counts = run_counts(qc, 32, SEED)
    if len(counts) != 1:
        raise RuntimeError(f"expected one deterministic outcome, got {counts}")
    return register_values(next(iter(counts)), qc)
