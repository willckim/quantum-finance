"""Part 4 -- Shor's 9-qubit code: nesting two codes to correct any single-qubit error.

Part of the error-correction walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from typing import Callable, Sequence

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.circuit import Qubit

from noise_and_tools import (SEED, STATEVECTOR_SIM, Check, Qubits, Sweep, agreement_check,
                             depolarizing_channel, deterministic_registers, inject_noise,
                             inject_paulis, parity_pair, percent, print_error_rate_sweep,
                             register_values, run_counts, run_sweep, section)
from pauli_frame import (break_even_point, build_decoder, exact_logical_error_curve, make_css_code,
                         validate_code)
from repetition_code import (REP3_CORRECTION, correct_bit_flip, decode_repetition,
                             encode_repetition, measure_bit_parities)


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
    print(f"\n  KEY RESULT: break-even at p* = {percent(p_star)} under code-capacity depolarizing")
    print("  noise (perfect syndrome checks; noisy checks on real hardware would lower it).")
    print("  Below it, 9 physical qubits make a better qubit than 1. The first code ever to do")
    print("  that against every kind of single-qubit error, not just one kind.")
    return sweep, exact, checks
