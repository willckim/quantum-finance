from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
import math

# THE NEEDLE IN THE HAYSTACK
# 4 qubits = 16 possible values (0-15)
# We're hiding the winner at position 7
target = 7
num_qubits = 4
total_items = 2 ** num_qubits

qc = QuantumCircuit(num_qubits, num_qubits)

# Put all qubits in superposition — search everything at once
qc.h(range(num_qubits))

# How many times to amplify the answer
num_iterations = math.isqrt(total_items)

for _ in range(num_iterations):
    # ORACLE — marks the target by flipping its phase
    target_bits = format(target, f'0{num_qubits}b')
    for i, bit in enumerate(target_bits[::-1]):
        if bit == '0':
            qc.x(i)
    qc.h(num_qubits - 1)
    qc.mcx(list(range(num_qubits - 1)), num_qubits - 1)
    qc.h(num_qubits - 1)
    for i, bit in enumerate(target_bits[::-1]):
        if bit == '0':
            qc.x(i)

    # DIFFUSION — amplifies the marked answer
    qc.h(range(num_qubits))
    qc.x(range(num_qubits))
    qc.h(num_qubits - 1)
    qc.mcx(list(range(num_qubits - 1)), num_qubits - 1)
    qc.h(num_qubits - 1)
    qc.x(range(num_qubits))
    qc.h(range(num_qubits))

qc.measure(range(num_qubits), range(num_qubits))

sim = AerSimulator()
result = sim.run(qc, shots=1000).result()
counts = result.get_counts()

print(f"Searching for {target} among {total_items} items\n")
print("Results (top 5):")
for value, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]:
    decimal = int(value, 2)
    bar = "█" * (count // 20)
    print(f"  {decimal:>2} ({value}): {count:>4} hits {bar}")

print(f"\nClassical: up to {total_items} guesses needed")
print(f"Quantum:   ~{num_iterations} iterations used")