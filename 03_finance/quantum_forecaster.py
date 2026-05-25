"""Quantum vs Classical revenue forecaster -- a learning project.

The goal: take 24 months of (made-up) monthly revenue and forecast the next
3 months two different ways, then compare them side by side.

  1. CLASSICAL model -- scikit-learn LinearRegression. Fits a straight trend
     line through the data and extends it forward. This is what 99% of
     business forecasting actually uses.

  2. QUANTUM model -- a Variational Quantum Regressor (VQR) from Qiskit
     Machine Learning. This is a tiny "quantum neural network": data is
     encoded into a qubit's rotation angle, a trainable circuit transforms
     it, and a classical optimizer tunes the circuit to fit the data.

Plain-language glossary (you are learning, not a physicist):
  - Qubit            : a quantum bit. Here we use just ONE.
  - Feature map      : the circuit that turns a number (the month) into a
                       rotation angle on the qubit. This is how data "enters"
                       the quantum computer.
  - Ansatz           : a circuit with adjustable knobs (parameters). Training
                       means finding the knob settings that fit the data.
  - Expectation value: the number we read back out, always between -1 and +1.
                       That is why we scale revenue into [-1, 1] before
                       training and scale the answer back afterwards.

Honest expectation: linear regression is built to extrapolate a trend, so it
will track the rising revenue well. The quantum model is built to *interpolate*
patterns inside the range it saw; an angle-encoded qubit cannot extrapolate a
straight trend, so its future numbers will flatten or curve instead of climbing.
Seeing that difference IS the lesson.

Run:  python quantum_forecaster.py
Output: a printed comparison + forecast_comparison.png in this folder.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

# Use a non-interactive backend so the chart saves to a file without trying
# to pop open a GUI window (important when running from a plain terminal).
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import MinMaxScaler

from qiskit.circuit.library import z_feature_map, real_amplitudes
from qiskit_machine_learning.algorithms import VQR
from qiskit_machine_learning.optimizers import COBYLA
from qiskit_machine_learning.utils import algorithm_globals

# A fixed random seed makes every run reproducible (same data, same fit).
SEED = 42
algorithm_globals.random_seed = SEED

HERE = Path(__file__).resolve().parent
CHART_PATH = HERE / "forecast_comparison.png"
FORECAST_MONTHS = [25, 26, 27]


def make_revenue_data(n_months: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """Invent 24 months of realistic monthly revenue ($80K-$150K, rising).

    Built from three pieces accountants would recognize:
      - a steady upward trend (the business is growing),
      - a mild seasonal wave (some months are busier),
      - small random noise (the real world is never perfectly smooth).
    """
    rng = np.random.default_rng(SEED)
    months = np.arange(1, n_months + 1)

    trend = 84_000 + 2_300 * (months - 1)          # grows ~$2.3K per month
    seasonal = 3_000 * np.sin(2 * np.pi * months / 12)  # yearly up/down cycle
    noise = rng.normal(0, 1_500, size=n_months)     # small monthly wiggle

    revenue = np.round((trend + seasonal + noise) / 100) * 100  # nearest $100
    revenue = np.clip(revenue, 80_000, 150_000)
    return months, revenue


def forecast_classical(months: np.ndarray, revenue: np.ndarray,
                       future: np.ndarray) -> np.ndarray:
    """Fit a straight line to the data and read off the next 3 months.

    LinearRegression works directly on raw numbers -- no scaling needed.
    """
    model = LinearRegression()
    model.fit(months.reshape(-1, 1), revenue)
    return model.predict(future.reshape(-1, 1))


def forecast_quantum(months: np.ndarray, revenue: np.ndarray,
                     future: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Train a 1-qubit Variational Quantum Regressor and forecast 3 months.

    Returns (in_sample_fit, future_prediction) so we can also draw what the
    quantum model learned over the training range.
    """
    # --- Scaling (the most important step for quantum ML) ---------------
    # The qubit only understands inputs as angles and outputs a number in
    # [-1, 1]. So we squeeze the month index into [0, 1] and revenue into
    # [-1, 1] before training, then undo the scaling on the predictions.
    x_scaler = MinMaxScaler(feature_range=(0, 1))
    y_scaler = MinMaxScaler(feature_range=(-1, 1))

    x_train = x_scaler.fit_transform(months.reshape(-1, 1))
    y_train = y_scaler.fit_transform(revenue.reshape(-1, 1))  # shape (n, 1)

    # --- Build the quantum circuit pieces -------------------------------
    # feature_map: encodes the (scaled) month onto 1 qubit as a rotation.
    #   reps=2 wraps the data in twice, giving the model a bit more to work
    #   with than a single rotation would.
    # ansatz: the trainable part. real_amplitudes is a standard, well-behaved
    #   choice of adjustable rotations -- these are the "knobs" we tune.
    feature_map = z_feature_map(feature_dimension=1, reps=2)
    ansatz = real_amplitudes(num_qubits=1, reps=3)

    # COBYLA is a simple, gradient-free optimizer -- robust for a tiny demo.
    # maxiter caps the training so the script always finishes quickly.
    vqr = VQR(
        feature_map=feature_map,
        ansatz=ansatz,
        optimizer=COBYLA(maxiter=200),
    )

    # Training: the optimizer nudges the circuit knobs until the quantum
    # output matches the (scaled) revenue as closely as it can.
    vqr.fit(x_train, y_train)

    # --- Predict, then scale the answers back into real dollars ---------
    def predict_dollars(x_raw: np.ndarray) -> np.ndarray:
        x_scaled = x_scaler.transform(x_raw.reshape(-1, 1))
        y_scaled = np.asarray(vqr.predict(x_scaled)).reshape(-1, 1)
        return y_scaler.inverse_transform(y_scaled).ravel()

    in_sample_fit = predict_dollars(months)   # what it learned on known months
    future_pred = predict_dollars(future)     # the 3-month forecast
    return in_sample_fit, future_pred


def print_comparison(months: np.ndarray, revenue: np.ndarray,
                     future: np.ndarray, classical: np.ndarray,
                     quantum: np.ndarray) -> None:
    """Print the training data and a side-by-side forecast table."""
    print("=" * 60)
    print("QUANTUM vs CLASSICAL  --  Monthly Revenue Forecaster")
    print(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 60)

    print(f"\nTraining data ({len(months)} months of actual revenue):")
    for m, r in zip(months, revenue):
        print(f"  Month {m:>2}: ${r:>10,.0f}")

    print("\nForecast for the next 3 months:")
    print(f"  {'Month':>5} | {'Classical (LinReg)':>20} | {'Quantum (VQR)':>16}")
    print(f"  {'-' * 5} | {'-' * 20} | {'-' * 16}")
    for m, c, q in zip(future, classical, quantum):
        print(f"  {m:>5} | {'$' + format(c, ',.0f'):>20} | {'$' + format(q, ',.0f'):>16}")

    print("\nWhat you are seeing:")
    print("  - Classical linear regression fits a straight trend line and")
    print("    extends it, so its forecast keeps climbing with the trend.")
    print("  - The quantum VQR learned the shape of the in-range data, but a")
    print("    1-qubit angle-encoded model cannot extrapolate a trend, so its")
    print("    future numbers flatten/curve instead of rising. That gap is the")
    print("    real lesson: today's small quantum models interpolate, they do")
    print("    not magically out-forecast a good classical baseline.")


def make_chart(months: np.ndarray, revenue: np.ndarray, future: np.ndarray,
               classical: np.ndarray, quantum: np.ndarray,
               quantum_fit: np.ndarray) -> None:
    """Draw training data + both forecasts and save to forecast_comparison.png."""
    fig, ax = plt.subplots(figsize=(11, 6))

    # Actual training revenue: solid line with dots.
    ax.plot(months, revenue, "-o", color="#1f4e79", linewidth=2,
            markersize=5, label="Actual revenue (training)")

    # Faint line showing what the quantum model learned in-range.
    ax.plot(months, quantum_fit, "--", color="#c0504d", alpha=0.45,
            linewidth=1.5, label="Quantum in-sample fit")

    # Connect the last real point to each forecast so the lines read clearly.
    bridge_x = np.concatenate(([months[-1]], future))
    ax.plot(bridge_x, np.concatenate(([revenue[-1]], classical)),
            "-s", color="#2e7d32", linewidth=2, markersize=7,
            label="Classical forecast (LinReg)")
    ax.plot(bridge_x, np.concatenate(([revenue[-1]], quantum)),
            "-^", color="#c0504d", linewidth=2, markersize=8,
            label="Quantum forecast (VQR)")

    # Shade the forecast region so it is obvious where prediction begins.
    ax.axvspan(months[-1], future[-1], color="gray", alpha=0.08)
    ax.axvline(months[-1] + 0.5, color="gray", linestyle=":", linewidth=1)

    ax.set_title("Revenue Forecast: Classical vs Quantum Machine Learning")
    ax.set_xlabel("Month")
    ax.set_ylabel("Monthly Revenue")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v/1000:,.0f}K"))
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(CHART_PATH, dpi=120)
    plt.close(fig)


def main() -> None:
    months, revenue = make_revenue_data(24)
    future = np.array(FORECAST_MONTHS)

    classical_pred = forecast_classical(months, revenue, future)
    quantum_fit, quantum_pred = forecast_quantum(months, revenue, future)

    print_comparison(months, revenue, future, classical_pred, quantum_pred)
    make_chart(months, revenue, future, classical_pred, quantum_pred, quantum_fit)
    print(f"\nChart saved to: {CHART_PATH}")


if __name__ == "__main__":
    main()
