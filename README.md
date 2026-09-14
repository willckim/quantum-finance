# quantum-finance

Learning quantum computing from zero and pointing it at real finance problems, one small program at a time.

## The story

I am an accountant on the CPA path. In 2026 I started learning quantum computing from scratch, with no physics background, to understand where it might actually matter for finance. This repo is the public record of that climb: it begins with the smallest possible quantum programs (a single coin flip on one qubit) and works up to three finance applications that compare a quantum approach against the classical method a real analyst would use today. Every script is heavily commented in plain language, because the comments are how I teach the concept to myself.

## What's inside

| Folder | What it covers |
|--------|----------------|
| `01_basics/` | Foundational quantum programs: superposition, entanglement, teleportation, and the Bernstein-Vazirani algorithm. The "hello world" layer. |
| `02_algorithms/` | Core quantum algorithms implemented from the gates up: Grover search and quantum error correction. |
| `03_finance/` | Three quantum-vs-classical finance applications, each producing a printed comparison and a chart. |

### `01_basics/`

- **`coin_flip.py`** — A Hadamard gate on one qubit creates a 50/50 superposition; measuring it many times shows a fair quantum coin.
- **`entangle.py`** — Builds a Bell state (Hadamard plus CNOT) so two qubits become perfectly correlated, the core of entanglement.
- **`teleport.py` / `teleport_clean.py`** — The quantum teleportation protocol, moving a qubit's state across the circuit using entanglement and classical bits.
- **`secret_number.py`** — Bernstein-Vazirani: a quantum circuit recovers a hidden bit string in a single query, no matter how long it is.
- **`compare.py`** — Runs Bernstein-Vazirani classically versus quantumly on a 104-bit secret, showing the classical side needs 104 queries while the quantum side needs one.

### `02_algorithms/`

- **`grover_search.py`** — Grover's search algorithm, which finds a marked item in an unsorted space in roughly the square root of the classical number of steps.
- **`quantum_error_correction.py`**: Builds quantum error correction up in six stages: a bare noisy qubit, the 3-qubit bit-flip and phase-flip codes, Shor's 9-qubit code, and a distance-3 rotated surface code. Each is hand-built with ancilla syndrome measurement and mid-circuit correction. It verifies that syndrome checks locate errors without collapsing the stored state, checks every simulated code against an exact enumeration of all error patterns, and plots logical vs physical error rate, including where the distance-3 and distance-5 surface codes cross (the threshold, under code-capacity noise). Output: `error_correction_threshold.png`.

### `03_finance/`

- **`quantum_forecaster.py`** — Forecasts the next three months of revenue two ways: a scikit-learn linear regression versus a one-qubit Variational Quantum Regressor from Qiskit Machine Learning. It demonstrates why an angle-encoded qubit interpolates patterns but cannot extrapolate a straight trend the way linear regression does. Output: `forecast_comparison.png`.
- **`portfolio_optimizer.py`** — Selects an optimal subset of assets by encoding the choice as a QUBO/Ising problem and solving it with a hand-built QAOA circuit, then checks the quantum result against a brute-force classical optimum. It shows how a combinatorial finance decision maps onto a quantum optimizer.
- **`quantum_monte_carlo.py`** — Estimates the probability that a small business runs low on cash during the year, comparing 10,000-path classical Monte Carlo against quantum amplitude estimation (maximum-likelihood, hand-built Grover operator). It makes the quadratic sampling speedup visible: quantum error falls like 1/M versus the classical 1/sqrt(N). Output: `risk_convergence.png`.

## How to run

```bash
# 1. Install dependencies (a virtual environment is recommended)
pip install -r requirements.txt

# 2. Run any program directly
python 01_basics/coin_flip.py
python 03_finance/quantum_monte_carlo.py
```

The two finance scripts that generate charts write their `.png` next to themselves in `03_finance/`. They use a non-interactive matplotlib backend, so they save the file without opening a window.

Note: `quantum_forecaster.py` additionally requires `scikit-learn` and `qiskit-machine-learning` (included in `requirements.txt`). It was developed under an earlier Python/Qiskit environment; the other scripts run on the environment below as-is.

## Environment

| Component | Version |
|-----------|---------|
| Python | 3.14.3 (Windows 11) |
| Qiskit | 2.3.1 |
| Qiskit Aer | 0.17.2 |
| NumPy | 2.4.4 |
| SciPy | 1.17.1 |
| Matplotlib | 3.10.9 |

All quantum circuits run on local simulators (Qiskit Aer and statevector). No quantum hardware or cloud account is required.

## About

I'm William Kim, an accountant learning quantum-finance applications in public. The aim is not to claim quantum advantage today; it is to build genuine intuition for how these algorithms work and where they could matter for financial modeling, by writing the code by hand and comparing it honestly against the classical baseline every time.
