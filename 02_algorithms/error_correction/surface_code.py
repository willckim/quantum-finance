"""Part 5 -- the distance-3 rotated surface code: local checks on a grid, syndromes on a lattice.

Part of the error-correction walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.quantum_info import Pauli, StabilizerState

from noise_and_tools import (P_SWEEP_LOG, SEED, SURFACE_D5_SAMPLES, Z_SCORE_LIMIT, Check, Qubits,
                             Sweep, agreement_check, depolarizing_channel, deterministic_registers,
                             inject_noise, inject_paulis, percent, print_error_rate_sweep,
                             run_sweep, section, z_score)
from pauli_frame import (CssCode, LookupDecoder, break_even_point, build_decoder,
                         exact_logical_error_curve, make_css_code, sampled_logical_error_rate,
                         validate_code)


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
