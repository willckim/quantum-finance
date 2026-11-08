"""Part 7 -- biased noise: when phase flips dominate, a repetition code can beat the surface code.

The idea behind cat qubits (Alice & Bob, AWS Ocelot). Part of the error-correction
walkthrough; run everything with run_all.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb, log
from typing import Callable, Mapping

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from scipy.optimize import brentq

from noise_and_tools import (BIAS_RATIOS, P_BIAS, P_SWEEP_EXACT, REP_LENGTHS, SEED, SHOTS,
                             SURFACE_D5_SAMPLES, Z_SCORE_LIMIT, Check, Qubits, bias_split,
                             biased_channel, deterministic_registers, failure_rate, inject_noise,
                             inject_paulis, percent, run_counts, section, seed_for, z_score)
from pauli_frame import (CssCode, LookupDecoder, build_decoder, exact_biased_curve,
                         exact_logical_error_curve, make_css_code, min_undetectable_logical_weight,
                         sampled_biased_error_rate, validate_code)
from phase_flip_code import PHASE_CODE
from repetition_code import rep3_closed_form
from surface_code import (apply_lookup_correction, logical_bell_prep, rotated_surface_code,
                          surface_memory_circuit)


# ======================================================================================
# PART 7 -- BIASED NOISE AND THE IDEA BEHIND CAT QUBITS
# ======================================================================================
# WHY BIAS CHANGES THE ANSWER
#   Parts 4-6 assumed X, Y and Z errors are equally likely. The surface code pays for that
#   assumption: it needs a 2D grid, 2d^2 - 1 qubits, because it must catch BOTH kinds of
#   error. Part 3 showed the cheap alternative, a repetition code, catches only ONE kind.
#   So the question is simple: what if the hardware almost never makes the other kind?
#   Then the cheap code is enough, and every qubit the surface code spends on the rare
#   error type is wasted.
#
#   The phase-flip repetition code of length n uses n data + (n - 1) check qubits:
#   5 for n = 3, 13 for n = 7. It corrects up to (n - 1) // 2 phase flips and NO bit
#   flips: an odd number of X or Y errors goes straight through as a logical error (pairs
#   cancel). Its failure rate is therefore two pieces added together:
#       phase part   ~ C(n, (n+1)/2) * P(Z)^((n+1)/2)   shrinks fast as n grows
#       bit-flip part ~ n * (P(X) + P(Y))                GROWS with n, never corrected
#   With no bias the second piece swamps everything and the code is worse than a bare
#   qubit. As the bias ratio eta grows, the bit-flip piece falls like 1/eta, and the
#   repetition code overtakes a surface code three times its size. At any finite eta the
#   bit-flip piece still sets a floor, so there is a best length: past it, adding qubits
#   adds more unprotected bit-flip targets than it removes phase failures.
#
# CAT QUBITS, IN PLAIN LANGUAGE
#   An ordinary qubit (a transmon) is a two-level circuit, and nothing about it prefers
#   one error type. A CAT QUBIT stores 0 and 1 as two opposite states of a microwave field
#   in a resonator, a wave swinging "left" (|+alpha>) or "right" (|-alpha>). To flip the
#   bit, noise must drag the whole field from one side to the other. The further apart
#   the two states (the more photons), the harder that is, and the bit-flip rate falls
#   EXPONENTIALLY with photon number. Phase flips, which scramble only the sign between
#   the two states, get worse merely LINEARLY with photon number. Turning up the photon
#   count therefore buys a huge, tunable bias by design: a little more phase noise in
#   exchange for nearly no bit flips.
#
#   Once bit flips are that rare, one line of cat qubits running a phase-flip repetition
#   code does the job a 2D surface code does for ordinary qubits (Guillaud & Mirrahimi,
#   PRX 2019). That is Alice & Bob's roadmap, and AWS's Ocelot chip (Putterman et al.,
#   Nature 2025) ran exactly this: cat qubits in a distance-5 repetition code. The pitch is
#   the qubit count. This part shows the same thing in miniature: with the default settings,
#   13 qubits against 49.
#
# THE CATCH THIS SCRIPT DOES NOT MODEL
#   Bias is only useful if it SURVIVES the gates. The checks need CNOTs, and on a
#   two-level qubit a CNOT is a continuous rotation: a phase error that strikes halfway
#   through comes out the other side as a bit flip, and the bias leaks away. Cat qubits
#   can perform a CNOT that keeps bit flips suppressed throughout (a "bias-preserving"
#   gate; Puri et al., Science Advances 2020). That property, not the bias itself, is the
#   hard part, and it is why this trick needs special hardware rather than better
#   software on transmons.
#
# NOISE MODEL FOR EVERY NUMBER IN THIS PART -- READ BEFORE TRUSTING ANY OF THEM
#   * CODE-CAPACITY, exactly as Parts 2-6: errors strike the data qubits once; the checks,
#     corrections and readout are perfect. Real devices are harsher (see run_all.py).
#   * Each data qubit independently suffers X, Y or Z, total probability p = P_BIAS (5%
#     by default), held fixed while the bias changes. Only the split moves.
#   * Bias ratio eta = P(Z) / P(X), with P(Y) = P(X) because Y also flips the bit.
#     eta = 1 is depolarizing noise. Papers often define eta = P(Z) / (P(X) + P(Y)),
#     half this value (noise_and_tools.bias_split has the conversion).
#   * Every decoder is the minimum-weight lookup table of Section 0b, decoding X and Z
#     errors separately. It does not know the bias exists and ignores Y correlations.
#   * The surface code is the standard rotated CSS code of Part 5. It gains NOTHING from
#     bias here; tailored variants (the XZZX surface code, Bonilla Ataides et al., Nature
#     Communications 2021) do, and would narrow the gap shown below.

BIG_ETA = 1e12          # stands in for "infinite bias" in the limit checks
MAX_ENUMERATED_N = 9    # exact enumeration holds 4^n patterns in memory: 262,144 at n = 9


def validate_bias_config() -> None:
    """Refuse settings the comparisons below cannot honour, instead of misreporting them."""
    if BIAS_RATIOS[0] != 1 or list(BIAS_RATIOS) != sorted(BIAS_RATIOS):
        raise ValueError(f"BIAS_RATIOS must ascend from 1 (the unbiased baseline): {BIAS_RATIOS}")
    if (any(n % 2 == 0 or not 3 <= n <= MAX_ENUMERATED_N for n in REP_LENGTHS)
            or list(REP_LENGTHS) != sorted(REP_LENGTHS)):
        raise ValueError("REP_LENGTHS must be ascending odd lengths from 3 to "
                         f"{MAX_ENUMERATED_N}: {REP_LENGTHS}")


def phase_repetition_code(n: int) -> CssCode:
    """Length-n phase-flip repetition code: X-type checks X_i X_(i+1), no Z-type checks."""
    return make_css_code(f"phase-flip repetition n={n}", n,
                         x_checks=[(i, i + 1) for i in range(n - 1)], z_checks=[],
                         logical_x=([], list(range(n))), logical_z=([0], []))


def repetition_qubits(n: int) -> int:
    """n data qubits plus n - 1 check qubits."""
    return 2 * n - 1


def surface_qubits(d: int) -> int:
    """Rotated surface code: d^2 data qubits plus d^2 - 1 check qubits."""
    return 2 * d * d - 1


def majority_failure(n: int, p: float) -> float:
    """Textbook: chance that more than half of n independent qubits flipped."""
    return sum(comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range((n + 1) // 2, n + 1))


def rep_failure_by_counting(n: int, p: float, eta: float) -> float:
    """Phase-flip repetition code under biased noise, by direct counting (no decoder).

    The code survives only if an EVEN number of qubits got a bit-flip part (X or Y; an odd
    number flips the logical, and nothing checks for it) AND at most (n - 1) // 2 got a
    phase part (Y or Z; majority vote fixes the rest). Y belongs to both, so the two
    conditions are not independent: walk the qubits one at a time tracking (bit-flip
    parity, phase count). Fast for any n, and shares nothing with the enumeration.
    """
    p_x, p_y, p_z = bias_split(p, eta)
    table = np.zeros((2, n + 1))
    table[0, 0] = 1.0
    for _ in range(n):
        step = (1 - p) * table
        step[1:, :] += p_x * table[:1, :]
        step[:1, :] += p_x * table[1:, :]
        step[1:, 1:] += p_y * table[:1, :-1]
        step[:1, 1:] += p_y * table[1:, :-1]
        step[:, 1:] += p_z * table[:, :-1]
        table = step
    return float(1.0 - table[0, :(n - 1) // 2 + 1].sum())


def best_repetition_length(eta: float, max_n: int = 41) -> int:
    """Odd length with the lowest failure rate at p = P_BIAS (n = 1 is a bare qubit)."""
    return min(range(1, max_n + 1, 2), key=lambda n: rep_failure_by_counting(n, P_BIAS, eta))


def css_memory_circuit(code: CssCode, decoder: LookupDecoder,
                       add_errors: Callable[[QuantumCircuit, Qubits], None]) -> QuantumCircuit:
    """One noisy memory cycle for any CSS code, scored against a noiseless reference qubit.

    Same recipe as Part 5's surface_memory_circuit (Bell pair with a reference, noise,
    measure every check, correct, undo the Bell pair, all-zeros readout = success), driven
    by the check matrices so it works for codes with no Z-type checks at all.
    """
    n = code.n
    data, ref = QuantumRegister(n, "data"), QuantumRegister(1, "ref")
    readout = ClassicalRegister(n + 1, "readout")
    qc = QuantumCircuit(data, ref, readout)
    scored = list(data) + [ref[0]]
    prep = logical_bell_prep(code)
    qc.compose(prep, scored, inplace=True)
    add_errors(qc, data)
    for kind, checks, table, gate in (("x", code.x_checks, decoder.z_correction, "z"),
                                      ("z", code.z_checks, decoder.x_correction, "x")):
        if checks.shape[0] == 0:
            continue
        anc = QuantumRegister(checks.shape[0], f"{kind}_anc")
        reg = ClassicalRegister(checks.shape[0], f"{kind}_checks")
        qc.add_register(anc)
        qc.add_register(reg)
        for k, row in enumerate(checks):
            if kind == "x":
                qc.h(anc[k])
            for q in np.flatnonzero(row):
                if kind == "x":
                    qc.cx(anc[k], data[int(q)])       # phase kickback collects the X parity
                else:
                    qc.cx(data[int(q)], anc[k])       # ancilla collects the Z parity
            if kind == "x":
                qc.h(anc[k])
            qc.measure(anc[k], reg[k])
        apply_lookup_correction(qc, data, reg, table, gate)
    qc.compose(prep.inverse(), scored, inplace=True)
    qc.measure(scored, readout)
    return qc


@dataclass(frozen=True)
class BiasResults:
    """Logical error rates at p = P_BIAS for each code, keyed by name."""

    exact: Mapping[str, Callable[[float], float]]        # eta -> rate (exact enumeration)
    circuit: Mapping[str, tuple[float, ...]]              # Aer circuits, one per BIAS_RATIOS
    surface5: tuple[float, ...]                           # sampled, one per BIAS_RATIOS
    qubits: Mapping[str, int]


def rep_key(n: int) -> str:
    return f"rep{n}"


def crossover_eta(rep: Callable[[float], float], surface: Callable[[float], float],
                  hi: float) -> float | None:
    """An eta in [1, hi] where the repetition code drops below the surface code.

    brentq finds A root, not necessarily the first. Both curves here are monotone in eta
    (the repetition code falls, the surface code barely moves), so there is only one.
    """
    def gap(log_eta: float) -> float:
        return rep(float(np.exp(log_eta))) - surface(float(np.exp(log_eta)))

    if gap(0.0) <= 0:
        return 1.0
    if gap(log(hi)) > 0:
        return None
    return float(np.exp(brentq(gap, 0.0, log(hi))))


def bias_split_checks() -> list[Check]:
    p = P_BIAS
    good = all(abs(sum(bias_split(p, eta)) - p) < 1e-15
               and abs(bias_split(p, eta)[2] / bias_split(p, eta)[0] - eta) < 1e-9 * eta
               and bias_split(p, eta)[0] == bias_split(p, eta)[1] for eta in BIAS_RATIOS)
    try:
        bias_split(p, 0.0)
        rejects_zero = False
    except ValueError:
        rejects_zero = True
    depolarizing = all(abs(x - p / 3) < 1e-15 for x in bias_split(p, 1.0))
    return [
        Check("Part 7: bias split sums to p, P(Z)/P(X) = eta, P(Y) = P(X) at every eta", good,
              f"eta in {BIAS_RATIOS}"),
        Check("Part 7: eta = 1 splits p into thirds (depolarizing); eta <= 0 is refused",
              depolarizing and rejects_zero),
    ]


def code_definition_checks(codes: Mapping[str, CssCode]) -> list[Check]:
    rep3 = codes[rep_key(3)] if rep_key(3) in codes else phase_repetition_code(3)
    ours = (rep3.x_checks, rep3.z_checks, *rep3.logical_x, *rep3.logical_z)
    part3 = (PHASE_CODE.x_checks, PHASE_CODE.z_checks, *PHASE_CODE.logical_x,
             *PHASE_CODE.logical_z)
    same = all(np.array_equal(a, b) for a, b in zip(ours, part3))
    checks = [Check("Part 7: phase_repetition_code(3) is Part 3's phase-flip code", same)]
    for n in REP_LENGTHS:
        code = codes[rep_key(n)]
        checks += validate_code(code, expected_distance=1)
        d_z = min_undetectable_logical_weight(code.x_checks, (code.logical_x[0],
                                                             code.logical_z[0]), n)
        checks.append(Check(f"Part 7: rep n={n} has phase-flip distance {n}", d_z == n,
                            f"fewest Z errors that flip the logical: {d_z}"))
    return checks


def limit_checks(codes: Mapping[str, CssCode], decoders: Mapping[str, LookupDecoder],
                 biased: Mapping[str, Callable[[float, float], float]]) -> list[Check]:
    """The bias enumeration must reduce to independently derived curves at both ends.

    WHAT EACH CHECK GRADES (where its expected values come from):
      * rep codes vs direct counting, and eta -> infinity vs the binomial majority formula:
        independent of the enumeration; they grade the decoder's failure set AND the
        (a, b, c) bookkeeping for the repetition codes.
      * eta = 1 and eta -> infinity for the surface code: the expected curves come from the
        SAME logical_failures set, so they grade only the (a, b, c) bookkeeping and the bias
        weights. The surface code's failure set itself is graded by the Aer circuits and by
        Part 5, which run the real syndrome circuits.
    """
    grid = P_SWEEP_EXACT[1:]
    depolarizing = {k: exact_logical_error_curve(codes[k], decoders[k], "depolarizing")
                    for k in (rep_key(REP_LENGTHS[0]), "surface3")}
    depol_gap = max(abs(biased[k](float(p), 1.0) - curve(p))
                    for k, curve in depolarizing.items() for p in grid)
    pure_z_rep = max(abs(biased[rep_key(n)](float(p), BIG_ETA) - majority_failure(n, float(p)))
                     for n in REP_LENGTHS for p in grid)
    surface_phase = exact_logical_error_curve(codes["surface3"], decoders["surface3"], "phase_flip")
    pure_z_surface = max(abs(biased["surface3"](float(p), BIG_ETA) - surface_phase(p))
                         for p in grid)
    rep3_textbook = max(abs(majority_failure(3, float(p)) - rep3_closed_form(p)) for p in grid)
    counting_gap = max(abs(biased[rep_key(n)](float(p), eta)
                           - rep_failure_by_counting(n, float(p), eta))
                       for n in REP_LENGTHS for eta in BIAS_RATIOS for p in grid[::10])
    return [
        Check("Part 7: rep-code enumeration equals direct counting at every eta",
              counting_gap < 1e-12, f"max difference {counting_gap:.1e}"),
        Check("Part 7: at eta = 1 the bias enumeration equals the depolarizing curves",
              depol_gap < 1e-12,
              f"rep n={REP_LENGTHS[0]} and surface d=3, max difference {depol_gap:.1e}"),
        Check("Part 7: at eta -> infinity every rep code equals the binomial majority formula",
              pure_z_rep < 1e-9 and rep3_textbook < 1e-12,
              f"max difference {pure_z_rep:.1e}; n=3 formula = 3p^2 - 2p^3"),
        Check("Part 7: at eta -> infinity surface d=3 equals its pure phase-flip curve",
              pure_z_surface < 1e-9, f"max difference {pure_z_surface:.1e}"),
    ]


def circuit_sanity_checks(code: CssCode, decoder: LookupDecoder) -> list[Check]:
    """The generic circuit must fix what the code fixes AND fail where it should."""
    def readout(errors: dict[int, str]) -> int:
        qc = css_memory_circuit(code, decoder, lambda c, data: inject_paulis(c, data, errors))
        return deterministic_registers(qc)["readout"]

    clean = readout({}) == 0
    z_fixed = all(readout({q: "Z"}) == 0 for q in range(code.n))
    x_fails = all(readout({q: "X"}) != 0 for q in range(code.n))
    return [
        Check("Part 7: generic CSS circuit, no error -> readout all zeros", clean),
        Check(f"Part 7: generic circuit corrects every single Z on rep n={code.n}", z_fixed),
        Check(f"Part 7: generic circuit lets every single X through on rep n={code.n} "
              "(expected failure)", x_fails),
    ]


def run_bias_circuits(build: Callable[[float], QuantumCircuit], tag: int) -> tuple[float, ...]:
    rates = []
    for i, eta in enumerate(BIAS_RATIOS):
        qc = build(eta)
        counts = run_counts(qc, SHOTS, seed_for(tag, i))
        rates.append(failure_rate(counts, qc, lambda reg: reg["readout"] != 0))
    return tuple(rates)


def worst_z(rates: tuple[float, ...], exact: Callable[[float], float], shots: int) -> float:
    return max(z_score(r, exact(eta), shots) for eta, r in zip(BIAS_RATIOS, rates))


def print_bias_table(results: BiasResults) -> None:
    keys = [rep_key(n) for n in REP_LENGTHS] + ["surface3"]
    names = {**{rep_key(n): f"rep n={n}" for n in REP_LENGTHS}, "surface3": "surface d=3"}
    header = "".join(f"  {names[k] + ' (' + str(results.qubits[k]) + 'q)':>17}" for k in keys)
    d5 = f"surface d=5 ({results.qubits['surface5']}q)"
    print(f"  {'eta':>6}{header}  {d5:>18}")
    print(f"  {'':>6}" + "".join(f"  {'circuit / exact':>17}" for _ in keys)
          + f"  {'sampled':>18}")
    for i, eta in enumerate(BIAS_RATIOS):
        cells = "".join(f"  {results.circuit[k][i]:>8.4f}/{results.exact[k](eta):<8.4f}"
                        for k in keys)
        print(f"  {eta:>6g}{cells}  {results.surface5[i]:>18.5f}")


def sampler_check(codes: Mapping[str, CssCode], decoders: Mapping[str, LookupDecoder],
                  exact: Mapping[str, Callable[[float], float]], keys: tuple[str, ...]) -> Check:
    """Grade the sampler before trusting it at d=5.

    GOTCHA: the rotated surface code is symmetric under swapping X and Z, so a sampler that
    mixed up bit and phase flips would still match it exactly. The phase-flip repetition
    code is maximally lopsided, so it is graded too; between them, a swap cannot hide.
    """
    scores = []
    for j, key in enumerate(keys):
        rates = tuple(sampled_biased_error_rate(codes[key], decoders[key], P_BIAS, eta,
                                                SURFACE_D5_SAMPLES,
                                                np.random.default_rng([SEED, 73, j, k]))
                      for k, eta in enumerate(BIAS_RATIOS))
        scores.append(worst_z(rates, exact[key], SURFACE_D5_SAMPLES))
    return Check(f"Part 7: biased Pauli-frame sampler reproduces exact {' and '.join(keys)}",
                 max(scores) <= Z_SCORE_LIMIT,
                 f"worst {max(scores):.2f} standard errors over {len(BIAS_RATIOS)} bias levels")


def collect_results() -> tuple[BiasResults, list[Check]]:
    layout3, layout5 = rotated_surface_code(3), rotated_surface_code(5)
    codes = {rep_key(n): phase_repetition_code(n) for n in REP_LENGTHS}
    codes["surface3"] = layout3.code
    decoders = {k: build_decoder(c) for k, c in codes.items()}
    biased = {k: exact_biased_curve(c, decoders[k]) for k, c in codes.items()}
    checks = bias_split_checks() + code_definition_checks(codes)
    checks += limit_checks(codes, decoders, biased)
    shortest = rep_key(REP_LENGTHS[0])
    checks += circuit_sanity_checks(codes[shortest], decoders[shortest])

    exact = {k: (lambda eta, f=f: f(P_BIAS, eta)) for k, f in biased.items()}
    circuit = {rep_key(n): run_bias_circuits(
        lambda eta, n=n: css_memory_circuit(codes[rep_key(n)], decoders[rep_key(n)],
                                            lambda c, data: inject_noise(
                                                c, data, biased_channel(P_BIAS, eta))),
        tag=700 + n) for n in REP_LENGTHS}
    prep3 = logical_bell_prep(layout3.code)
    circuit["surface3"] = run_bias_circuits(
        lambda eta: surface_memory_circuit(layout3, prep3, decoders["surface3"],
                                           lambda c, data: inject_noise(
                                               c, data, biased_channel(P_BIAS, eta))),
        tag=750)
    for key, rates in circuit.items():
        worst = worst_z(rates, exact[key], SHOTS)
        checks.append(Check(f"Part 7: {key} Aer circuits under biased noise match exact "
                            "enumeration", worst <= Z_SCORE_LIMIT,
                            f"worst {worst:.2f} standard errors over {len(rates)} bias levels"))

    checks.append(sampler_check(codes, decoders, exact, (shortest, "surface3")))
    decoder5 = build_decoder(layout5.code)
    surface5 = tuple(sampled_biased_error_rate(layout5.code, decoder5, P_BIAS, eta,
                                               SURFACE_D5_SAMPLES,
                                               np.random.default_rng([SEED, 75, k]))
                     for k, eta in enumerate(BIAS_RATIOS))
    qubits = {**{rep_key(n): repetition_qubits(n) for n in REP_LENGTHS},
              "surface3": surface_qubits(3), "surface5": surface_qubits(5)}
    return BiasResults(exact, circuit, surface5, qubits), checks


def headline_checks(results: BiasResults) -> list[Check]:
    """Each claim the printout makes, tested on the numbers it was printed from."""
    lo, hi = BIAS_RATIOS[0], BIAS_RATIOS[-1]
    ex, short, long = results.exact, rep_key(REP_LENGTHS[0]), rep_key(REP_LENGTHS[-1])
    best_rep = min((rep_key(n) for n in REP_LENGTHS), key=lambda k: ex[k](hi))
    d5_hi = results.surface5[-1]
    d5_margin = Z_SCORE_LIMIT * np.sqrt(d5_hi * (1 - d5_hi) / SURFACE_D5_SAMPLES)
    lengths_lo = [ex[rep_key(n)](lo) for n in REP_LENGTHS]
    best = [best_repetition_length(eta) for eta in BIAS_RATIOS]
    d5_name = f"surface d=5 ({results.qubits['surface5']} qubits)"
    return [
        Check("Part 7: the best repetition length grows with bias, and is 1 (no code) without it",
              best[0] == 1 and all(a <= b for a, b in zip(best, best[1:]))
              and best[-1] > best[0],
              ", ".join(f"eta={eta:g}: n={n}" for eta, n in zip(BIAS_RATIOS, best))),
        Check(f"Part 7: without bias (eta={lo:g}) the surface code beats the repetition code",
              ex["surface3"](lo) < ex[short](lo),
              f"surface d=3 {ex['surface3'](lo):.4f} vs "
              f"rep n={REP_LENGTHS[0]} {ex[short](lo):.4f}"),
        Check(f"Part 7: at eta={hi:g} the {results.qubits[short]}-qubit repetition code beats "
              f"the {results.qubits['surface3']}-qubit surface code",
              ex[short](hi) < ex["surface3"](hi),
              f"{ex[short](hi):.4f} vs {ex['surface3'](hi):.4f}"),
        Check(f"Part 7: at eta={hi:g} the best repetition code ({results.qubits[best_rep]} "
              f"qubits) beats sampled {d5_name} beyond sampling error",
              ex[best_rep](hi) < d5_hi - d5_margin,
              f"{ex[best_rep](hi):.5f} vs {d5_hi:.5f} +- {d5_margin:.5f}"),
        Check("Part 7: the CSS surface code gains nothing from bias",
              ex["surface3"](hi) >= ex["surface3"](lo),
              f"d=3: {ex['surface3'](lo):.4f} at eta={lo:g}, "
              f"{ex['surface3'](hi):.4f} at eta={hi:g}"),
        Check("Part 7: without bias, longer repetition codes are WORSE (bit-flip floor)",
              all(a < b for a, b in zip(lengths_lo, lengths_lo[1:])),
              " < ".join(f"{r:.3f}" for r in lengths_lo)),
        Check(f"Part 7: at eta={hi:g} the longest repetition code beats the shortest",
              ex[long](hi) < ex[short](hi), f"{ex[long](hi):.5f} vs {ex[short](hi):.5f}"),
    ]


def print_findings(results: BiasResults) -> None:
    hi, ex = BIAS_RATIOS[-1], results.exact
    print(f"\n  Crossover: the eta above which each repetition code beats surface d=3 "
          f"({results.qubits['surface3']} qubits), p = {P_BIAS:.0%}:")
    for n in REP_LENGTHS:
        eta = crossover_eta(ex[rep_key(n)], ex["surface3"], hi)
        where = f"eta ~ {eta:,.1f}" if eta is not None else f"never below eta = {hi:g}"
        print(f"    rep n={n} ({repetition_qubits(n):>2} qubits): {where}")
    best = min(REP_LENGTHS, key=lambda n: ex[rep_key(n)](hi))
    print(f"\n  KEY RESULT at eta = {hi:g}, p = {P_BIAS:.0%} (code-capacity noise):")
    print(f"    rep n={best} uses {repetition_qubits(best)} qubits and fails "
          f"{percent(ex[rep_key(best)](hi), 3)}; surface d=5 uses {results.qubits['surface5']} "
          "and fails "
          f"{percent(results.surface5[-1], 3)}.")
    print(f"    Without bias (eta = 1) the same repetition code fails "
          f"{percent(ex[rep_key(best)](BIAS_RATIOS[0]), 1)}, worse than a bare qubit "
          f"({P_BIAS:.0%}).")
    print("    The surface code does not care about bias: under pure phase flips all of p")
    print("    lands on the one error type it has distance 3 (or 5) against, so it gets")
    print("    slightly WORSE. The repetition code spends every qubit on the error that happens.")
    print("    Longer is not always better: each extra qubit is another unprotected bit-flip")
    print("    target, so at finite bias the bit-flip floor picks a best length (direct count):")
    for eta in BIAS_RATIOS:
        n = best_repetition_length(eta)
        print(f"      eta = {eta:>6g}: best n = {n:>2} ({repetition_qubits(n):>2} qubits), fails "
              f"{percent(rep_failure_by_counting(n, P_BIAS, eta), 3)}")
    print("    Caveat: real hardware needs bias-preserving gates (cat qubits have them,")
    print("    ordinary qubits do not), and noisy checks lower every number here.")


def part7_biased_noise() -> tuple[BiasResults, list[Check]]:
    section("PART 7 -- BIASED NOISE: the idea behind cat qubits")
    validate_bias_config()
    print(f"Code-capacity noise, total p = {P_BIAS:.0%} per data qubit, split by the bias ratio")
    print("eta = P(Z) / P(X), with P(Y) = P(X). eta = 1 is depolarizing noise.\n")
    for eta in BIAS_RATIOS:
        p_x, p_y, p_z = bias_split(P_BIAS, eta)
        print(f"  eta = {eta:>6g}:  P(X) = {p_x:.6f}  P(Y) = {p_y:.6f}  P(Z) = {p_z:.6f}")
    results, checks = collect_results()
    print(f"\n(a) Logical error rate at p = {P_BIAS:.0%}: full Qiskit Aer circuits "
          f"({SHOTS:,} shots)")
    print(f"    vs exact enumeration, and the {surface_qubits(5)}-qubit d=5 surface code by "
          "Pauli-frame sampling")
    print(f"    ({SURFACE_D5_SAMPLES:,} samples). q = physical qubits including check qubits.\n")
    print_bias_table(results)
    print_findings(results)
    return results, checks + headline_checks(results)
