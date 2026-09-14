"""Part 2 -- the 3-qubit repetition code: finding a bit flip without reading the data.

The conceptual heart of the walkthrough: no-cloning, logical vs physical qubits, and why a
syndrome measurement does not collapse the stored state. Run everything with run_all.py.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.quantum_info import DensityMatrix, Operator, Statevector, partial_trace, state_fidelity

from noise_and_tools import (P_SWEEP_EXACT, SEED, STATEVECTOR_SIM, Check, Qubits, Sweep,
                             agreement_check, bit_flip_channel, inject_noise, inject_paulis,
                             parity_pair, percent, print_error_rate_sweep, register_values,
                             run_sweep, section)
from pauli_frame import (break_even_point, build_decoder, exact_logical_error_curve, make_css_code,
                         min_weight_table, validate_code)


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
    print(f"\n  KEY RESULT: break-even at p* = {percent(p_star, 1)} under code-capacity")
    print("  noise, where the parity checks never fail. Solving 3p^2 - 2p^3 = p gives")
    print("  p = 0, 1/2 or 1, so the encoded qubit beats the bare one at EVERY p below 50%")
    print("  and loses above it (at p = 0.6 the code fails 64.8% of the time vs 60.0% bare).")
    print("  Why so generous? This model gives the code PERFECT parity checks. On hardware the")
    print("  ancillas, CNOTs and measurements fail too, and a lying syndrome turns the 'fix'")
    print("  into an error. Under that circuit-level noise the surface code's threshold is about")
    print("  1%, versus about 13% in this script's code-capacity model (see Part 6).")
    return sweep, enumerated, checks
