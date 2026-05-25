"""Quantum (QAOA) vs Classical (brute force) portfolio optimizer -- a learning project.

THE PROBLEM
-----------
We have $100,000 and 15 stocks. We want to pick an allocation that keeps RISK
(variance) low while aiming for an 8% expected return, with no single stock
holding more than 15% of the money.

WHY "PICK EXACTLY 7 STOCKS, EQUAL WEIGHT"
-----------------------------------------
A quantum optimizer like QAOA works on YES/NO (binary) choices -- "is this stock
in the portfolio or not?" -- one qubit per stock. It cannot natively handle a
smooth dial like "put 11.3% here, 7.8% there."

So we turn the money question into a clean binary one. The 15% cap means the most
concentrated legal portfolio holds at least ceil(100% / 15%) = 7 stocks. We fix
the count at exactly 7 and weight them equally: 100,000 / 7 = $14,285.71 each =
14.29% per stock. That respects the 15% cap, spends the whole budget, and -- crucially
-- keeps the math "quadratic" so QAOA can encode it. Each of the 15 stocks is one
qubit: 1 = hold it, 0 = skip it.

HOW BOTH OPTIMIZERS SOLVE THE *SAME* OBJECTIVE
----------------------------------------------
We score every candidate portfolio x (a 15-bit yes/no vector) with one number:

    score(x) =  A * variance(x)                  <- we want this small (low risk)
              + B * (return(x) - 0.08)^2          <- penalty for missing the 8% target
              + P * (number_of_holdings(x) - 7)^2 <- penalty unless exactly 7 are held

Lower score = better. The classical optimizer computes this for ALL 2^15 = 32,768
portfolios and keeps the best. QAOA encodes the same score into a quantum circuit
and searches it differently. Same yardstick -> a fair head-to-head.

PLAIN-LANGUAGE QUANTUM GLOSSARY
-------------------------------
  Qubit         : a quantum bit. Here, 15 of them -- one per stock.
  Superposition : one qubit can represent 0 AND 1 at once, so 15 qubits hold all
                  32,768 portfolios simultaneously as a weighted blend.
  QUBO / Ising  : the score() above, rewritten as a math object the circuit can use.
  Cost layer    : circuit gates that "tag" good portfolios with a phase based on score.
  Mixer layer   : circuit gates that let the blend shift toward the tagged-good ones.
  QAOA          : Cost + Mixer layers repeated p times, with a handful of dial settings
                  (angles) that a classical optimizer tunes to concentrate the blend
                  onto the best portfolios. Then we measure to read out an answer.

Run:  python portfolio_optimizer.py
"""

from __future__ import annotations

import warnings
from collections import Counter
from datetime import datetime

import numpy as np
from scipy.optimize import minimize

from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp, Statevector

warnings.filterwarnings("ignore")  # keep the learning output clean

# ----------------------------- configuration -------------------------------
SEED = 7
N_STOCKS = 15
BUDGET = 100_000.0
HOLDINGS = 7              # exactly 7 stocks (14.29% each, satisfies the 15% cap)
TARGET_RETURN = 0.08      # 8% expected annual return target
MAX_WEIGHT = 0.15         # 15% cap per stock (used only to validate the design)

# Objective weights (tuned so the optimum holds exactly 7 stocks near the target).
W_RISK = 1.0              # A: emphasis on minimizing variance
W_RETURN = 25.0           # B: emphasis on hitting the 8% return target
W_CARDINALITY = 2.0       # P: must dominate so "exactly 7" is enforced

TICKERS = ["ARVO", "BLZN", "CRTX", "DLTA", "EVRG", "FYNT", "GRDN", "HLIO",
           "INVX", "JUNO", "KLRA", "LMNS", "MTRX", "NOVA", "ORBT"]


# --------------------------- 1. market data --------------------------------
def generate_market_data(seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """Invent realistic expected returns and a valid covariance matrix.

    - Expected annual returns: 4% to 14% (typical equity range).
    - Volatilities: 12% to 35% (typical equity range).
    - Correlations come from a small "factor model" so the covariance matrix is
      mathematically valid (positive semi-definite) -- a real requirement, or the
      risk math produces nonsense.
    """
    rng = np.random.default_rng(seed)
    mu = rng.uniform(0.04, 0.14, N_STOCKS)            # expected annual return per stock
    vol = rng.uniform(0.12, 0.35, N_STOCKS)           # annual volatility per stock

    # Build a realistic correlation matrix via 4 hidden market "factors", then
    # shrink toward the identity so it is well-conditioned and every diagonal = 1.
    loadings = rng.normal(0, 1, (N_STOCKS, 4))
    factor_cov = loadings @ loadings.T
    d = np.sqrt(np.diag(factor_cov))
    corr = factor_cov / np.outer(d, d)
    corr = 0.7 * corr + 0.3 * np.eye(N_STOCKS)        # diagonal stays 1.0

    cov = np.outer(vol, vol) * corr                   # covariance = vol_i*vol_j*corr_ij
    return mu, cov


def portfolio_metrics(x: np.ndarray, mu: np.ndarray,
                      cov: np.ndarray) -> tuple[float, float]:
    """Return (expected_return, risk) for a chosen set of stocks, equal-weighted.

    risk = standard deviation = sqrt(variance). Returns (0, 0) for an empty set.
    """
    k = int(x.sum())
    if k == 0:
        return 0.0, 0.0
    w = x / k                                         # equal weight across the k holdings
    exp_return = float(w @ mu)
    variance = float(w @ cov @ w)
    return exp_return, float(np.sqrt(variance))


# ------------------------- 2. the shared objective -------------------------
def build_qubo(mu: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, float]:
    """Encode score(x) as a symmetric matrix Q so that score(x) = x^T Q x + offset.

    Every piece below is quadratic in the yes/no vector x, which is exactly what
    both the brute-force scorer and the quantum circuit need. We use the FIXED
    weight 1/7 (not 1/k) so the formula stays quadratic; the cardinality penalty
    then forces the answer to actually hold 7 stocks, making 1/7 correct.
    """
    n = N_STOCKS
    b = HOLDINGS
    ones = np.ones(n)

    # (A) Risk term: variance with weight 1/b  ->  (1/b^2) * x^T cov x
    q_risk = cov / (b * b)

    # (B) Return-target term: ((1/b) mu.x - target)^2
    #     = x^T (a a^T) x  - 2*target*(a.x) + target^2,  where a = mu / b
    a = mu / b
    q_return = np.outer(a, a)
    lin_return = -2.0 * TARGET_RETURN * a            # linear part (goes on the diagonal)
    const_return = TARGET_RETURN ** 2

    # (C) Cardinality term: (sum(x) - b)^2 = x^T (1 1^T) x - 2b*(sum x) + b^2
    q_card = np.outer(ones, ones)
    lin_card = -2.0 * b * ones
    const_card = b * b

    # Combine quadratic parts with their emphasis weights.
    Q = W_RISK * q_risk + W_RETURN * q_return + W_CARDINALITY * q_card

    # Fold linear parts onto the diagonal (valid because x_i^2 == x_i for 0/1).
    lin = W_RETURN * lin_return + W_CARDINALITY * lin_card
    Q = Q + np.diag(lin)

    offset = W_RETURN * const_return + W_CARDINALITY * const_card
    return Q, float(offset)


def score(x: np.ndarray, Q: np.ndarray, offset: float) -> float:
    """The single number both optimizers minimize: score(x) = x^T Q x + offset."""
    return float(x @ Q @ x + offset)


# --------------------------- 3. classical solver ---------------------------
def brute_force(Q: np.ndarray, offset: float) -> tuple[np.ndarray, int]:
    """Check EVERY one of the 2^15 portfolios and return the best plus the count.

    This is the gold standard: it cannot miss the true optimum because it looks at
    all of them. The catch is that 2^N explodes -- fine at 15 stocks (32,768), but
    hopeless at, say, 100 stocks (2^100 is more portfolios than atoms on Earth).
    """
    n = N_STOCKS
    total = 1 << n                                    # 2^15 = 32,768
    idx = np.arange(total)
    # Expand each integer 0..32767 into its 15 bits: X[row, j] = bit j (stock j).
    X = ((idx[:, None] >> np.arange(n)[None, :]) & 1).astype(float)
    scores = np.einsum("ti,ij,tj->t", X, Q, X) + offset
    best = X[int(np.argmin(scores))]
    return best, total


# ----------------------------- 4. QAOA solver ------------------------------
def qubo_to_ising(Q: np.ndarray) -> tuple[np.ndarray, dict[tuple[int, int], float], float]:
    """Translate the QUBO matrix into 'Ising' coefficients the qubits understand.

    Swap each 0/1 variable x_i for a spin z_i in {+1,-1} via x_i = (1 - z_i)/2.
    Algebra gives:  x^T Q x = const + sum_i h_i z_i + sum_{i<j} J_ij z_i z_j
      h_i   = -(column sum of Q)_i / 2      (linear coefficient per qubit)
      J_ij  =  Q_ij / 2                     (coupling per pair, Q is symmetric)
      const = (sum(Q) + trace(Q)) / 4       (a constant energy shift)
    The const does not change which portfolio is best, but we return it so the
    self-check can confirm the translation is exact. These h/J are literally the
    rotation angles the cost layer applies.
    """
    n = Q.shape[0]
    colsum = Q.sum(axis=0)                            # sum over rows for each column
    h = -colsum / 2.0                                 # linear coefficient per qubit
    J: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            coupling = (Q[i, j] + Q[j, i]) / 2.0      # symmetric pair coefficient
            if abs(coupling / 2.0) > 1e-12:
                J[(i, j)] = coupling / 2.0
    const = float((Q.sum() + np.trace(Q)) / 4.0)
    return h, J, const


def ising_hamiltonian(h: np.ndarray, J: dict[tuple[int, int], float]) -> SparsePauliOp:
    """Build the quantum 'cost meter' (Hamiltonian) whose lowest energy = best portfolio."""
    terms = [("Z", [i], float(h[i])) for i in range(len(h)) if abs(h[i]) > 1e-12]
    terms += [("ZZ", [i, j], float(c)) for (i, j), c in J.items()]
    return SparsePauliOp.from_sparse_list(terms, num_qubits=len(h))


def qaoa_circuit(h: np.ndarray, J: dict[tuple[int, int], float],
                 gammas: np.ndarray, betas: np.ndarray) -> QuantumCircuit:
    """Construct the QAOA circuit by hand so every gate is visible.

    Step 1: Hadamard on all qubits -> an even blend of all 32,768 portfolios.
    Step 2 (repeated p times):
        Cost layer  -- RZ/RZZ rotations tag portfolios by how good their score is.
                       (Exact here because the cost Hamiltonian is diagonal.)
        Mixer layer -- RX rotations let probability flow toward the better-tagged ones.
    """
    n = len(h)
    qc = QuantumCircuit(n)
    qc.h(range(n))                                    # uniform superposition
    for gamma, beta in zip(gammas, betas):
        for i in range(n):                            # cost: single-qubit phases
            if abs(h[i]) > 1e-12:
                qc.rz(2.0 * gamma * h[i], i)
        for (i, j), c in J.items():                   # cost: pairwise phases
            qc.rzz(2.0 * gamma * c, i, j)
        for i in range(n):                            # mixer
            qc.rx(2.0 * beta, i)
    return qc


def bitstring_to_x(bitstring: str) -> np.ndarray:
    """Decode a measured qubit string into a stock yes/no vector.

    Qiskit prints bitstrings little-endian (rightmost char = qubit 0), so we
    reverse before indexing: x[i] = the bit for qubit i = stock i.
    """
    rev = bitstring[::-1]
    return np.array([int(rev[i]) for i in range(len(rev))], dtype=float)


def qaoa_optimize(Q: np.ndarray, offset: float, reps: int = 4,
                  shots: int = 4096, restarts: int = 4) -> tuple[np.ndarray, int, int]:
    """Run QAOA: tune the angles, then measure to read out the best portfolio.

    Returns (best_x, expectation_evaluations, distinct_portfolios_examined).
    """
    rng = np.random.default_rng(SEED)
    h, J, _ = qubo_to_ising(Q)
    hamiltonian = ising_hamiltonian(h, J)

    eval_count = {"n": 0}

    def expected_score(theta: np.ndarray) -> float:
        # theta packs the dials: first `reps` gammas (cost), then `reps` betas (mixer).
        gammas, betas = theta[:reps], theta[reps:]
        state = Statevector(qaoa_circuit(h, J, gammas, betas))
        eval_count["n"] += 1
        return float(state.expectation_value(hamiltonian).real)

    # A classical optimizer (COBYLA) hunts for the angle settings that push the
    # quantum blend toward low-score portfolios. We try a few random starts and
    # keep the best, since this landscape can have local dips.
    best_theta, best_val = None, np.inf
    for _ in range(restarts):
        theta0 = rng.uniform(0, np.pi, 2 * reps)
        res = minimize(expected_score, theta0, method="COBYLA",
                       options={"maxiter": 250})
        if res.fun < best_val:
            best_val, best_theta = res.fun, res.x

    # With the angles fixed, "measure" the circuit many times. Each measurement
    # collapses the blend to one portfolio; good portfolios show up most often.
    gammas, betas = best_theta[:reps], best_theta[reps:]
    final_state = Statevector(qaoa_circuit(h, J, gammas, betas))
    probs = final_state.probabilities_dict()
    keys = list(probs.keys())
    p = np.array([probs[k] for k in keys])
    draws = rng.choice(len(keys), size=shots, p=p / p.sum())
    measured = Counter(keys[d] for d in draws)        # finite-shot measurement

    # Classically score only the portfolios we actually measured, keep the best.
    best_x, best_score = None, np.inf
    for bitstring in measured:
        x = bitstring_to_x(bitstring)
        s = score(x, Q, offset)
        if s < best_score:
            best_score, best_x = s, x
    return best_x, eval_count["n"], len(measured)


# ------------------------------ 5. reporting -------------------------------
def describe(label: str, x: np.ndarray, mu: np.ndarray, cov: np.ndarray,
             combos: int, combos_note: str) -> None:
    """Print one optimizer's chosen portfolio and its stats."""
    ret, risk = portfolio_metrics(x, mu, cov)
    held = [i for i in range(N_STOCKS) if x[i] == 1]
    per_stock = BUDGET / len(held) if held else 0.0

    print(f"\n{label}")
    print("-" * len(label))
    print(f"  Holdings ({len(held)} stocks, ${per_stock:,.2f} = "
          f"{per_stock / BUDGET:.1%} each):")
    for i in held:
        print(f"    {TICKERS[i]:<6} ${per_stock:>11,.2f}   "
              f"(expected return {mu[i]:.1%})")
    print(f"  Expected portfolio return : {ret:.2%}  (target {TARGET_RETURN:.0%})")
    print(f"  Expected portfolio risk   : {risk:.2%}  (annual std dev)")
    print(f"  Combinations evaluated    : {combos:,}  ({combos_note})")


def main() -> None:
    print("=" * 64)
    print("QUANTUM (QAOA) vs CLASSICAL (BRUTE FORCE) PORTFOLIO OPTIMIZER")
    print(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    print(f"Budget ${BUDGET:,.0f} | {N_STOCKS} stocks | exactly {HOLDINGS} held | "
          f"<= {MAX_WEIGHT:.0%}/stock | target {TARGET_RETURN:.0%}")
    print("=" * 64)

    mu, cov = generate_market_data()
    Q, offset = build_qubo(mu, cov)

    # Internal consistency check: confirm the QUBO and Ising encodings agree, so
    # both solvers truly optimize the identical objective (trust, but verify).
    h, J, const = qubo_to_ising(Q)
    rng = np.random.default_rng(0)
    ok = True
    for _ in range(200):
        x = rng.integers(0, 2, N_STOCKS).astype(float)
        z = 1 - 2 * x                                 # x=1 -> spin -1
        ising_energy = float(h @ z + sum(c * z[i] * z[j] for (i, j), c in J.items()))
        # x^T Q x must equal the Ising energy plus the constant shift.
        if abs((score(x, Q, offset) - offset) - (ising_energy + const)) > 1e-6:
            ok = False
            break
    print(f"\nEncoding consistency check (QUBO == Ising): {'PASS' if ok else 'FAIL'}")

    # Classical: exhaustive search over all portfolios.
    bf_x, bf_combos = brute_force(Q, offset)

    # Quantum: QAOA proposes; we read out by measurement.
    qaoa_x, qaoa_evals, qaoa_distinct = qaoa_optimize(Q, offset)

    describe("CLASSICAL  --  Brute Force", bf_x, mu, cov, bf_combos,
             "all 2^15 portfolios, optimum guaranteed")
    describe("QUANTUM    --  QAOA", qaoa_x, mu, cov, qaoa_distinct,
             f"distinct portfolios measured; {qaoa_evals} circuit evaluations to tune angles")

    same = bool(np.array_equal(bf_x, qaoa_x))
    print("\n" + "=" * 64)
    print("VERDICT")
    print("=" * 64)
    print(f"  QAOA matched the exact optimum: {'YES' if same else 'NO'}")
    print(f"  Brute force examined every one of {bf_combos:,} portfolios.")
    print(f"  QAOA held all {bf_combos:,} in superposition at once, tuned its angles")
    print(f"  with {qaoa_evals} evaluations, and read the answer from "
          f"{qaoa_distinct:,} measured portfolios.")
    print("  At 15 stocks brute force still wins easily. The point of QAOA is what")
    print("  happens at 100+ stocks: brute force's 2^N becomes impossible, while the")
    print("  quantum circuit's size grows only linearly with the number of stocks.")


if __name__ == "__main__":
    main()
