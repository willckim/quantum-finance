"""The quantum kernel: a ZZ feature-map circuit, its overlap as a similarity, and its cost.

WHAT A QUANTUM KERNEL IS
  A kernel method (here a support vector machine) never looks at transactions directly; it
  only needs a SIMILARITY score between every pair. A quantum kernel computes that score on
  a quantum computer:
    1. Encode transaction x as a quantum state |psi(x)> by running a circuit whose rotation
       angles are x's features (one qubit per feature, so 6 features -> 6 qubits).
    2. Similarity k(x, y) = |<psi(x)|psi(y)>|^2: how much the two states overlap, from 1
       (identical) down to 0. On hardware it is measured by running x's circuit, then y's
       circuit backwards, and counting how often every qubit comes back 0.
  The hope is that the circuit maps transactions into a space (2^6 = 64 complex dimensions
  here) where fraud separates more cleanly than any classical similarity could manage.

THE CIRCUIT: qiskit's zz_feature_map (Havlicek et al., Nature 2019)
  Per repetition: H on every qubit, phase 2 x_i on qubit i, then for every pair (i, j) a
  CNOT, phase 2 (pi - x_i)(pi - x_j), CNOT. The pair terms entangle the qubits, which is
  what makes the kernel hard to compute classically at large qubit counts.

HOW THIS SCRIPT COMPUTES IT, AND HOW THAT IS CHECKED
  Scoring 57,000 test transactions against 400 training ones is 23 million overlaps.
  Simulating 23 million circuits one by one would take hours, so feature_states() builds
  the same statevectors with numpy: each repetition is "Hadamard on every qubit, then a
  diagonal phase", and the phase of basis state b is
      sum_i 2 x_i b_i  +  sum_{i<j} 2 (pi - x_i)(pi - x_j) (b_i XOR b_j).
  feature_map_checks() compares that against qiskit's own circuit, simulated gate by gate,
  and also confirms the comparison would catch a map with the entangling terms missing.
  aer_kernel_check() then measures some overlaps the hardware way, with shots.

THE BANDWIDTH PROBLEM
  Features are multiplied by a bandwidth before encoding. Too wide and every pair of
  transactions looks unrelated (kernel ~ 0 off the diagonal, the model memorises); too
  narrow and every pair looks identical. For this data, validation tuning picks a very
  NARROW bandwidth, where the kernel behaves almost like a simple classical one (Shaydulin
  & Wild, PRA 2022, show this in general). Narrow also means the useful signal sits in
  tiny differences between overlaps near 1, which is exactly what shot noise on real
  hardware blurs: shot_noise_kernel() measures that.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit.library import zz_feature_map
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator

from config import AER_PAIRS, AER_SHOTS, SEED, Z_SCORE_LIMIT, Check, check


def basis_bits(n_qubits: int) -> np.ndarray:
    """Row k holds the bits of basis state k, qubit 0 first (qiskit's little-endian order)."""
    return ((np.arange(2 ** n_qubits)[:, None] >> np.arange(n_qubits)) & 1).astype(float)


def hadamard_all(n_qubits: int) -> np.ndarray:
    h = np.array([[1.0, 1.0], [1.0, -1.0]]) / np.sqrt(2)
    out = h
    for _ in range(n_qubits - 1):
        out = np.kron(out, h)
    return out


def feature_states(F: np.ndarray, reps: int, entangle: bool = True) -> np.ndarray:
    """Statevectors of zz_feature_map for every row of F (rows = transactions)."""
    n = F.shape[1]
    bits = basis_bits(n)
    phase = 2 * F @ bits.T
    if entangle:
        for i, j in combinations(range(n), 2):
            parity = np.abs(bits[:, i] - bits[:, j])
            phase += 2 * np.outer((np.pi - F[:, i]) * (np.pi - F[:, j]), parity)
    diagonal, h = np.exp(1j * phase), hadamard_all(n)
    psi = np.zeros((len(F), 2 ** n), dtype=complex)
    psi[:, 0] = 1.0
    for _ in range(reps):
        psi = diagonal * (psi @ h)
    return psi


def kernel(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """k(x, y) = |<psi(x)|psi(y)>|^2 for every (row of left, row of right)."""
    return np.abs(left.conj() @ right.T) ** 2


def circuit_state(x: np.ndarray, reps: int) -> np.ndarray:
    return Statevector(zz_feature_map(len(x), reps=reps).assign_parameters(x)).data


def feature_map_checks(n_qubits: int) -> list[Check]:
    """Fast numpy states vs qiskit's circuit, gate by gate. Expected values: qiskit."""
    rng = np.random.default_rng([SEED, 7])
    points = rng.normal(scale=1.5, size=(6, n_qubits))
    worst = {}
    for reps in (1, 2):
        fast = feature_states(points, reps)
        worst[reps] = min(abs(np.vdot(circuit_state(x, reps), s)) ** 2
                          for x, s in zip(points, fast))
    no_pairs = feature_states(points, 2, entangle=False)
    broken = min(abs(np.vdot(circuit_state(x, 2), s)) ** 2 for x, s in zip(points, no_pairs))
    return [check("Quantum: fast statevectors equal qiskit's zz_feature_map circuit "
                  "(reps 1 and 2)", min(worst.values()) > 1 - 1e-10,
                  f"worst fidelity {min(worst.values()):.12f} over {len(points)} points"),
            check("Quantum: the same comparison rejects a map missing its entangling terms",
                  broken < 0.99, f"lowest fidelity of the broken map {broken:.3f}")]


def kernel_checks(K: np.ndarray) -> list[Check]:
    eigen = float(np.linalg.eigvalsh(K).min())
    return [check("Quantum: training kernel symmetric, unit diagonal, entries in [0, 1]",
                  bool(np.allclose(K, K.T, atol=1e-12) and np.allclose(np.diag(K), 1.0)
                       and K.min() >= -1e-12 and K.max() <= 1 + 1e-12)),
            check("Quantum: training kernel is positive semidefinite (a valid kernel)",
                  eigen > -1e-8, f"smallest eigenvalue {eigen:.2e}")]


def overlap_circuit(x: np.ndarray, y: np.ndarray, reps: int) -> QuantumCircuit:
    """Hardware recipe: prepare psi(x), undo psi(y), measure. P(all zeros) = k(x, y)."""
    fmap = zz_feature_map(len(x), reps=reps)
    qc = fmap.assign_parameters(x).compose(fmap.assign_parameters(y).inverse())
    qc.measure_all()
    return qc


def aer_kernel_check(F: np.ndarray, reps: int) -> Check:
    """Measure kernel entries with shots on real circuits; compare with the exact overlaps."""
    rng = np.random.default_rng([SEED, 11])
    pairs = rng.choice(len(F), size=(AER_PAIRS, 2), replace=False)      # 40 distinct rows
    sim = AerSimulator(method="statevector")
    exact = kernel(feature_states(F[pairs[:, 0]], reps), feature_states(F[pairs[:, 1]], reps))
    scores = []
    for k, (a, b) in enumerate(pairs):
        counts = sim.run(overlap_circuit(F[a], F[b], reps), shots=AER_SHOTS,
                         seed_simulator=int(rng.integers(2 ** 31))).result().get_counts()
        observed = counts.get("0" * F.shape[1], 0) / AER_SHOTS
        expected = float(exact[k, k])
        std_err = np.sqrt(max(expected * (1 - expected), 1e-12) / AER_SHOTS)
        scores.append(abs(observed - expected) / std_err)
    return check("Quantum: shot-measured overlap circuits on Aer match the exact kernel",
                 max(scores) <= Z_SCORE_LIMIT,
                 f"{AER_PAIRS} pairs x {AER_SHOTS:,} shots, worst {max(scores):.2f} "
                 "standard errors")


def shot_noise_kernel(K: np.ndarray, shots: int, rng: np.random.Generator) -> np.ndarray:
    """What hardware would return: each entry estimated from `shots` all-zero counts."""
    return rng.binomial(shots, np.clip(K, 0.0, 1.0)) / shots


def hardware_circuit_count(n_train: int, n_support: int, n_scored: int) -> dict[str, int]:
    """Circuits a quantum computer would run: one per distinct pair it must compare."""
    return {"train": n_train * (n_train - 1) // 2, "score": n_support * n_scored}
