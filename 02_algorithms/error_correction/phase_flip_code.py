"""Part 3 -- phase flips: the error the bit-flip code cannot see, and the code that can.

Part of the error-correction walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from typing import Callable

from qiskit import ClassicalRegister, QuantumCircuit

from noise_and_tools import (P_SWEEP_EXACT, Check, Qubits, agreement_check, inject_noise,
                             parity_pair, phase_flip_channel, print_error_rate_sweep, run_sweep,
                             section)
from pauli_frame import (break_even_point, build_decoder, exact_logical_error_curve, make_css_code,
                         validate_code)
from repetition_code import (BIT_FLIP_OPS, REP3_CODE, REP3_CORRECTION, CodeOps, decode_repetition,
                             encode_repetition, measure_bit_parities, rep3_closed_form,
                             single_error_run, single_error_table, three_qubit_layout)


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
