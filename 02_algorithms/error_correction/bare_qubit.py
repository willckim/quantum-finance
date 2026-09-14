"""Part 1 -- the problem: a bare qubit under bit-flip noise is wrong with probability p.

Part of the error-correction walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister

from noise_and_tools import (P_SWEEP_CIRCUIT, SHOTS, Check, Sweep, agreement_check,
                             bit_flip_channel, failure_rate, inject_noise, run_counts, section,
                             seed_for)


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
