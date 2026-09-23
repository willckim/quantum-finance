"""Part 1 -- Why RSA breaks: a toy Shor's algorithm that factors N = 15.

THE ONE IDEA
------------
RSA is safe because multiplying two primes is easy and splitting the product back apart
is hard. Nobody has found a fast classical way to do it: the best known method (the
general number field sieve) takes time that grows almost exponentially with key length,
which is why a 2048-bit modulus is out of reach.

Shor (1994) showed that factoring reduces to a different question: PERIOD FINDING.

    Pick any a that shares no factor with N. The sequence a^1, a^2, a^3, ... (mod N)
    repeats with some period r. For N = 15 and a = 7:

        7^1 = 7,  7^2 = 49 = 4,  7^3 = 28 = 13,  7^4 = 91 = 1,  7^5 = 7, ...   so r = 4

    If r is even, then a^r - 1 = (a^(r/2) - 1)(a^(r/2) + 1) is a multiple of N, so each
    bracket very likely shares a factor with N, and gcd() (Euclid, instant) pulls it out:
        gcd(7^2 - 1, 15) = gcd(48, 15) = 3        gcd(7^2 + 1, 15) = gcd(50, 15) = 5

Finding r classically means walking the sequence, which is as hard as factoring. A quantum
computer finds it in a different way:

    1. Put a COUNTING register into a superposition of every exponent x at once.
    2. Compute a^x mod N into a WORK register, for all x in one pass. The two registers
       are now entangled: every x that gives the same remainder is linked together.
    3. The exponents that share a remainder are spaced exactly r apart, so the counting
       register now holds a pattern that repeats every r steps.
    4. The quantum Fourier transform (QFT) turns "repeats every r" into sharp peaks at
       multiples of 2^n / r, the same way a Fourier transform turns a steady tone into a
       single spike at its frequency.
    5. Measure. The result y is (almost always) close to k * 2^n / r for a random k, and
       a classical continued-fraction step reads r off y / 2^n.

Steps 1 to 4 are the only quantum part, and their cost grows only polynomially with the
size of N. That polynomial scaling, against the classical near-exponential, is the whole
threat: at scale, the same five steps factor an RSA modulus and recover the private key.
Elliptic-curve cryptography (ECDSA, ECDH, X25519) falls to a close cousin of the same
algorithm (the discrete-logarithm version), so it is just as exposed.

WHAT FACTORING 15 PROVES, AND WHAT IT DOES NOT
----------------------------------------------
This proves the MECHANISM: the circuit below really does find the period with
interference, and the classical steps really do turn that period into 3 x 5. It says
nothing about the THREAT LEVEL. Two shortcuts make N = 15 easy that RSA-2048 would not
allow:

  * The modular-multiplication step is built here as a 16 x 16 permutation matrix,
    computed classically from x -> a*x mod 15. That requires listing every input, which
    is fine for 16 values and impossible for 2^2048. A real attack must build modular
    arithmetic out of reversible adders (Beauregard 2003; Gidney 2025), which is where
    almost all of the qubits and time go.
  * The simulator is noiseless. Real hardware needs error-corrected LOGICAL qubits, and
    every logical qubit costs hundreds to thousands of physical ones (see this repo's
    02_algorithms/error_correction project for why).

The scale gap: this toy uses 8 counting + 4 work = 12 noiseless qubits. The RSA-2048
resource estimate, with its source, lives in sources.py (RSA_2048_ESTIMATE) and is
printed at the end of the demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd, pi

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import UnitaryGate
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator

from common import SEED, SHOR_SHOTS, Check, section
from sources import RSA_2048_ESTIMATE

N = 15
A = 7
N_COUNT = 8                         # counting qubits: resolves phases to 1/256
N_WORK = 4                          # 4 qubits hold remainders 0..15


# ======================================================================================
# THE QUANTUM PART: period finding
# ======================================================================================

def modmul_gate(a: int, modulus: int, n_work: int) -> UnitaryGate:
    """|y> -> |a*y mod N> as a permutation matrix (y >= N left alone).

    Multiplication by a is reversible because a has an inverse mod N, so this is a valid
    permutation and therefore a valid quantum gate. It is the toy shortcut described in
    the module docstring: correct, but built by listing every input classically.
    """
    dim = 2 ** n_work
    matrix = np.zeros((dim, dim))
    for y in range(dim):
        matrix[(a * y) % modulus if y < modulus else y, y] = 1.0
    return UnitaryGate(matrix, label=f"x{a} mod {modulus}")


def inverse_qft(qc: QuantumCircuit, qubits: list[int]) -> None:
    """Hand-built inverse QFT (textbook form, swaps first, then un-rotate and Hadamard)."""
    n = len(qubits)
    for i in range(n // 2):
        qc.swap(qubits[i], qubits[n - 1 - i])
    for j in range(n):
        for k in range(j):
            qc.cp(-pi / 2 ** (j - k), qubits[k], qubits[j])
        qc.h(qubits[j])


def period_finding_circuit(a: int, modulus: int = N, n_count: int = N_COUNT,
                           n_work: int = N_WORK, measure: bool = True) -> QuantumCircuit:
    qc = QuantumCircuit(n_count + n_work, n_count if measure else 0)
    counting = list(range(n_count))
    work = list(range(n_count, n_count + n_work))
    qc.h(counting)                          # step 1: every exponent x at once
    qc.x(work[0])                           # work register starts at |1>
    for j in counting:                      # step 2: a^x mod N, one bit of x at a time
        # Counting qubit j carries the 2^j bit of x, so it controls multiplication by
        # a^(2^j) mod N. Those powers come from classical repeated squaring (cheap).
        qc.append(modmul_gate(pow(a, 2 ** j, modulus), modulus, n_work).control(1),
                  [j, *work])
    inverse_qft(qc, counting)               # steps 3-4: turn the repeat into peaks
    if measure:
        qc.measure(counting, range(n_count))
    return qc


def exact_distribution(a: int) -> dict[int, float]:
    """Exact probability of each counting-register outcome, from the statevector."""
    state = Statevector(period_finding_circuit(a, measure=False))
    probs = state.probabilities(list(range(N_COUNT)))
    return {y: float(p) for y, p in enumerate(probs) if p > 1e-9}


def sample(a: int, shots: int = SHOR_SHOTS) -> dict[int, int]:
    sim = AerSimulator()
    qc = transpile(period_finding_circuit(a), sim, seed_transpiler=SEED)
    counts = sim.run(qc, shots=shots, seed_simulator=SEED).result().get_counts()
    return {int(bits, 2): n for bits, n in counts.items()}


# ======================================================================================
# THE CLASSICAL PART: from a measured y to the factors
# ======================================================================================

@dataclass(frozen=True)
class Readout:
    y: int
    phase: Fraction           # y / 2^n
    guess: Fraction           # closest fraction with denominator <= N
    r: int | None             # period, if the denominator passes a^r = 1 (mod N)


def read_period(y: int, a: int, modulus: int = N, n_count: int = N_COUNT) -> Readout:
    phase = Fraction(y, 2 ** n_count)
    guess = phase.limit_denominator(modulus)          # continued fractions
    r = guess.denominator
    return Readout(y, phase, guess, r if pow(a, r, modulus) == 1 else None)


def factors_from_period(a: int, r: int, modulus: int = N) -> tuple[int, int] | None:
    """Shor's classical finish. Fails (returns None) for odd r or a^(r/2) = -1 mod N."""
    if r % 2:
        return None
    half = pow(a, r // 2, modulus)
    if half == modulus - 1:
        return None
    p, q = gcd(half - 1, modulus), gcd(half + 1, modulus)
    return (p, q) if 1 < p < modulus and p * q == modulus else None


def classical_order(a: int, modulus: int = N) -> int:
    """Brute-force period by walking the sequence: the independent reference answer."""
    value, r = a % modulus, 1
    while value != 1:
        value, r = (value * a) % modulus, r + 1
    return r


# ======================================================================================
# THE DEMO
# ======================================================================================

def theory_peaks(a: int) -> dict[int, float]:
    """The exact distribution the math predicts, computed WITHOUT running any circuit.

    With period r dividing 2^n, the counting register lands only on the r evenly spaced
    values k * 2^n / r, each with probability exactly 1/r.
    """
    r = classical_order(a)
    if (2 ** N_COUNT) % r:
        raise ValueError(f"period {r} does not divide 2^{N_COUNT}; peaks are not exact")
    return {k * 2 ** N_COUNT // r: 1 / r for k in range(r)}


def matches_theory(dist: dict[int, float], theory: dict[int, float]) -> float | None:
    """Largest probability error against theory, or None if the outcomes differ."""
    if set(dist) != set(theory):
        return None
    return max(abs(dist[y] - p) for y, p in theory.items())


def _print_run(counts: dict[int, int]) -> tuple[int | None, tuple[int, int] | None]:
    print(f"\n  Measured counting register ({SHOR_SHOTS} shots):")
    print(f"  {'y':>5} {'shots':>6} {'y/256':>8} {'closest r/N fraction':>21} {'period?':>9}")
    periods = set()
    for y in sorted(counts):
        ro = read_period(y, A)
        periods |= {ro.r} - {None}
        verdict = f"r = {ro.r}" if ro.r else "no"
        print(f"  {y:>5} {counts[y]:>6} {float(ro.phase):>8.4f} {str(ro.guess):>21} "
              f"{verdict:>9}")
    r = min(periods) if periods else None
    factors = factors_from_period(A, r) if r else None
    print(f"\n  Period found: r = {r}.  Check: {A}^{r} mod {N} = {pow(A, r, N) if r else '-'}")
    if factors:
        half = pow(A, r // 2, N)
        print(f"  Classical finish: {A}^{r // 2} mod {N} = {half};  "
              f"gcd({half} - 1, {N}) = {factors[0]},  gcd({half} + 1, {N}) = {factors[1]}")
        print(f"  RESULT: {N} = {factors[0]} x {factors[1]}")
    print("  Note: y = 0 and y = 128 are real outcomes that do not reveal r on their own")
    print("  (0/256 carries no information; 128/256 = 1/2 suggests r = 2, which fails the")
    print("  a^r = 1 test). Shor's algorithm simply reruns; a success needs y = 64 or 192.")
    return r, factors


def _every_a() -> dict[int, tuple[dict[int, float], int | None, tuple[int, int] | None]]:
    print(f"\n  Every a coprime to {N} (the algorithm picks a at random):")
    per_a = {}
    for a in (a for a in range(2, N) if gcd(a, N) == 1):
        dist = exact_distribution(a)
        found = {read_period(y, a).r for y in dist} - {None}
        r_a = min(found) if found else None
        f = factors_from_period(a, r_a) if r_a else None
        per_a[a] = (dist, r_a, f)
        outcome = f"{f[0]} x {f[1]}" if f else "trivial factors, pick another a"
        print(f"    a = {a:>2}: period {r_a}  ->  {outcome}")
    return per_a


def _shor_checks(r, factors, counts, per_a) -> list[Check]:
    # Reference 1: brute-force classical order, computed without any quantum code.
    checks = [Check(f"Shor: circuit period for a={A} matches brute-force order",
                    r == classical_order(A), f"circuit {r}, classical {classical_order(A)}"),
              Check("Shor: recovered factors multiply to 15 and are 3 and 5",
                    factors is not None and sorted(factors) == [3, 5], str(factors))]
    # Reference 2: analytic theory (theory_peaks), not the circuit. Checked for EVERY a,
    # on the whole distribution: reading only the period back out of a distribution can
    # succeed even when the distribution is wrong.
    errors = {a: matches_theory(dist, theory_peaks(a)) for a, (dist, _, _) in per_a.items()}
    bad = [a for a, e in errors.items() if e is None or e > 1e-9]
    worst = max((e for e in errors.values() if e is not None), default=float("nan"))
    checks.append(Check("Shor: every coprime a gives exactly the r peaks of 1/r theory predicts",
                        not bad, f"{len(per_a)} values of a, max error {worst:.1e}"
                        + (f"; WRONG for a = {bad}" if bad else "")))
    # Sampled shots must land only on the theoretical peaks, near 25% each.
    theory = theory_peaks(A)
    frac = {y: counts.get(y, 0) / SHOR_SHOTS for y in theory}
    stray = sum(n for y, n in counts.items() if y not in theory)
    sigma = (0.25 * 0.75 / SHOR_SHOTS) ** 0.5
    checks.append(Check("Shor: sampled shots sit on the 4 peaks within 4 standard errors",
                        stray == 0 and all(abs(f - 0.25) < 4 * sigma for f in frac.values()),
                        ", ".join(f"{y}:{f:.3f}" for y, f in frac.items())))
    # a = 14 is the known failure: 14 = -1 mod 15, so a^(r/2) + 1 is a multiple of 15.
    checks.append(Check("Shor: a=14 correctly reported as a failure (a^(r/2) = -1 mod 15)",
                        per_a[14][2] is None and all(per_a[a][2] for a in per_a if a != 14)))
    return checks


def part1_shor() -> tuple[dict, list[Check]]:
    section(f"PART 1 -- WHY RSA BREAKS: Shor's algorithm factors N = {N} with a = {A}")
    qc = period_finding_circuit(A)
    print(f"  Circuit: {N_COUNT} counting + {N_WORK} work qubits, "
          f"{qc.size()} operations before compilation.")
    counts = sample(A)
    r, factors = _print_run(counts)
    per_a = _every_a()
    print(f"\n  Scale check: this toy used {N_COUNT + N_WORK} noiseless qubits.")
    print(f"  {RSA_2048_ESTIMATE.sentence}")
    summary = {"N": N, "a": A, "period": r, "factors": factors,
               "qubits": N_COUNT + N_WORK, "rsa_2048": RSA_2048_ESTIMATE.sentence}
    return summary, _shor_checks(r, factors, counts, per_a)
