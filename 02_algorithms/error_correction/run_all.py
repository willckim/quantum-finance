"""Quantum error correction from the ground up -- a learning project.

WHAT THIS PROJECT BUILDS, AND WHERE EACH PART LIVES
---------------------------------------------------
Six stages. Each one fixes the weakness the stage before it exposed. Read the modules in this
order; run_all.py (this file) runs them all and prints the self-verification report.

    Part 1  bare_qubit.py        One bare qubit under bit-flip noise is wrong with probability p.
    Part 2  repetition_code.py   Spread one qubit across three. Parity checks find a bit flip
                                 without ever reading the stored value.
    Part 3  phase_flip_code.py   The bit-flip code is blind to phase errors (shown failing);
                                 its Hadamard-rotated twin catches them.
    Part 4  shor_code.py         Nest the two codes. Corrects X, Y or Z on any one qubit.
    Part 5  surface_code.py      Distance-3 rotated surface code: 9 data qubits on a grid,
                                 8 local stabilizer checks, syndromes drawn on the lattice.
    Part 6  threshold_sweep.py   error_correction_threshold.png: logical vs physical error rate.

    Shared  noise_and_tools.py   Settings, simulators, noise channels, result-reading helpers.
            pauli_frame.py       Exact classical reference model, lookup decoder, fast sampler.

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

Run:  py run_all.py      (writes error_correction_threshold.png next to it)
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Sequence

import numpy as np
import qiskit
import qiskit_aer

from bare_qubit import part1_bare_qubit
from noise_and_tools import SEED, Check, percent, section
from pauli_frame import break_even_point, build_decoder, exact_logical_error_curve
from phase_flip_code import PHASE_CODE, part3_phase_flips
from repetition_code import part2_bit_flip_code
from shor_code import part4_shor_code
from surface_code import part5_surface_code
from threshold_sweep import ThresholdAnalysis, part6_threshold_chart


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
