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
#### Quantum error correction: `02_algorithms/error_correction/`

**What it demonstrates.** How a quantum computer can protect fragile qubits without ever copying them or looking at the information they hold. The project builds error correction up in six stages, from one noisy qubit to the surface code the industry is scaling today. Every encoder, syndrome measurement and decoder is hand-built in Qiskit, and every simulated result is checked against exact math (58 self-checks, printed at the end of each run).

**Start here.** Open [`run_all.py`](02_algorithms/error_correction/run_all.py) first. Its opening comment explains the five ideas the project is about (no-cloning, physical vs logical qubits, syndrome measurement, threshold, overhead) and lists the modules in reading order. Then read the parts in order:

| Part | File | What it shows |
|------|------|---------------|
| 1 | `bare_qubit.py` | An unprotected qubit is wrong with probability p. |
| 2 | `repetition_code.py` | The 3-qubit code finds a bit flip without collapsing the stored state, verified with state fidelity. The conceptual heart of the project. |
| 3 | `phase_flip_code.py` | The bit-flip code is blind to phase errors; its Hadamard-rotated twin catches them. |
| 4 | `shor_code.py` | Shor's 9-qubit code corrects X, Y and Z errors on any qubit, including a continuous rotation. |
| 5 | `surface_code.py` | A distance-3 rotated surface code, with its syndromes drawn on the 2D lattice. |
| 6 | `threshold_sweep.py` | The threshold chart and the physical-qubit overhead arithmetic. |
| shared | `noise_and_tools.py`, `pauli_frame.py` | Settings, noise channels and helpers, plus the exact classical model the circuits are graded against. |

**What the threshold chart shows.** [`error_correction_threshold.png`](02_algorithms/error_correction/error_correction_threshold.png) plots logical error rate against physical error rate p. Panel A asks whether one encoded qubit beats a bare one: a code helps wherever its curve sits below the diagonal, and the crossing is that code's break-even point. Panel B asks whether a *bigger* code helps: below the point where the distance-3 and distance-5 surface-code curves cross, the larger code is more reliable. That crossing is the threshold, and operating below it is what lets error correction scale.

![Logical vs physical error rate for the 3-qubit, Shor and surface codes](02_algorithms/error_correction/error_correction_threshold.png)

**Read every number with its noise model.** The simulation uses *code-capacity* noise: errors strike the data qubits, but the syndrome measurements that detect them are perfect. Real hardware faces *circuit-level* noise, where the ancilla qubits, gates and measurements doing the checking fail too, and that pulls every number down.

| Result | Code-capacity noise (this simulation) | Circuit-level noise (real hardware) |
|--------|---------------------------------------|-------------------------------------|
| 3-qubit code break-even | 50% (bit-flip noise only) | Lower once the checks can fail; not simulated here |
| Shor 9-qubit / surface d=3 break-even | 8.5% / 8.3% (depolarizing noise) | Lower; not simulated here |
| Surface code threshold | About 13.3% (d=3 vs d=5 crossing) | About 1% (Wang, Fowler & Hollenberg 2011; Fowler et al. 2012) |

The 13.3% figure is a property of the idealized model, not a claim about real devices. The hardware-relevant surface-code threshold is about 1%.

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

# 3. Run the whole error-correction walkthrough (about 35 seconds)
python 02_algorithms/error_correction/run_all.py
```

The finance scripts that generate charts write their `.png` next to themselves in `03_finance/`, and `run_all.py` writes `error_correction_threshold.png` next to itself. All of them use a non-interactive matplotlib backend, so they save the file without opening a window.

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
