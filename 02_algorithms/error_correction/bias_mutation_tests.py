"""Mutation tests for Part 7: proof that its self-checks can fail.

A check that has never been seen failing is not yet evidence. Each mutant below is a
plausible wrong implementation; running Part 7 against it must turn at least one check
FAIL. The real code must pass everything. Not run by run_all.py (each mutant reruns
Part 7, about 30 seconds); run it after changing biased_noise.py or the bias helpers:

    py bias_mutation_tests.py
"""

from __future__ import annotations

import contextlib
import io
import sys
from math import comb
from typing import Callable

import numpy as np

import biased_noise
import noise_and_tools
import pauli_frame


def failing_checks() -> list[str]:
    with contextlib.redirect_stdout(io.StringIO()):
        _, checks = biased_noise.part7_biased_noise()
    return [check.name for check in checks if not check.passed]


@contextlib.contextmanager
def patched(targets: list[tuple[object, str]], replacement: Callable):
    originals = [(module, name, getattr(module, name)) for module, name in targets]
    for module, name in targets:
        setattr(module, name, replacement)
    try:
        yield
    finally:
        for module, name, original in originals:
            setattr(module, name, original)


def split_half_eta(p: float, eta: float) -> tuple[float, float, float]:
    """Tuckett's eta = P(Z) / (P(X) + P(Y)) used where this script's eta was meant."""
    bit = p / (2 * eta + 2)
    return bit, bit, p - 2 * bit


def sampler_swapping_x_and_z(code, decoder, p, eta, samples, rng) -> float:
    p_x, p_y, _ = noise_and_tools.bias_split(p, eta)
    u = rng.random((samples, code.n))
    ez = (u < p_x + p_y).astype(np.uint8)
    ex = ((u >= p_x) & (u < p)).astype(np.uint8)
    return float(pauli_frame.logical_failures(code, decoder, ex, ez).mean())


def counting_ignoring_y_overlap(n: int, p: float, eta: float) -> float:
    """Treats 'odd bit-flip parts' and 'phase majority fails' as independent events."""
    p_x, p_y, p_z = noise_and_tools.bias_split(p, eta)
    even_bits = (1 + (1 - 2 * (p_x + p_y)) ** n) / 2
    phase_ok = sum(comb(n, k) * (p_y + p_z) ** k * (1 - p_y - p_z) ** (n - k)
                   for k in range((n - 1) // 2 + 1))
    return 1 - even_bits * phase_ok


MUTANTS = (
    ("eta convention halved everywhere it is shared",
     [(pauli_frame, "bias_split"), (biased_noise, "bias_split")], split_half_eta),
    ("Pauli-frame sampler swaps bit and phase flips",
     [(biased_noise, "sampled_biased_error_rate")], sampler_swapping_x_and_z),
    ("memory circuit never applies the correction",
     [(biased_noise, "apply_lookup_correction")], lambda *args, **kwargs: None),
    ("direct count ignores that Y is both a bit and a phase flip",
     [(biased_noise, "rep_failure_by_counting")], counting_ignoring_y_overlap),
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ok = True
    baseline = failing_checks()
    print(f"[{'PASS' if not baseline else 'FAIL'}] real code: all Part 7 checks pass"
          + (f" (failing: {baseline})" if baseline else ""))
    ok &= not baseline
    for name, targets, replacement in MUTANTS:
        with patched(targets, replacement):
            caught = failing_checks()
        ok &= bool(caught)
        print(f"[{'PASS' if caught else 'FAIL'}] mutant killed: {name} "
              f"({len(caught)} check(s) failed)")
        for check in caught[:3]:
            print(f"        caught by: {check}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
