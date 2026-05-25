"""Quantum coin flip.

A Hadamard gate puts a single qubit into an equal superposition of |0> and |1>.
Measuring collapses it to one outcome with true 50/50 probability, drawn from
quantum randomness rather than a pseudo-random number generator.

Run directly:  python coin_flip.py
"""

from collections import Counter

from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

HEADS = "0"
TAILS = "1"
_LABELS = {HEADS: "HEADS", TAILS: "TAILS"}


def _build_circuit() -> QuantumCircuit:
    """One qubit: superposition via H, then measure into one classical bit."""
    qc = QuantumCircuit(1, 1)
    qc.h(0)
    qc.measure(0, 0)
    return qc


def flip(simulator: AerSimulator | None = None) -> str:
    """Flip once. Returns 'HEADS' or 'TAILS'."""
    sim = simulator or AerSimulator()
    result = sim.run(_build_circuit(), shots=1).result()
    outcome = next(iter(result.get_counts()))
    return _LABELS[outcome]


def fairness_check(shots: int = 10_000, simulator: AerSimulator | None = None) -> Counter:
    """Flip `shots` times and return a Counter of HEADS/TAILS to show the 50/50 split."""
    sim = simulator or AerSimulator()
    counts = sim.run(_build_circuit(), shots=shots).result().get_counts()
    return Counter({_LABELS[bit]: n for bit, n in counts.items()})


def main() -> None:
    sim = AerSimulator()

    print(f"Single flip: {flip(sim)}\n")

    shots = 10_000
    tally = fairness_check(shots, sim)
    print(f"Fairness check over {shots:,} flips:")
    for label in ("HEADS", "TAILS"):
        n = tally.get(label, 0)
        print(f"  {label}: {n:>6,}  ({n / shots:.1%})")


if __name__ == "__main__":
    main()
