from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

# CHANGE THIS - pick any combo of 0s and 1s
secret = "1011010011"

num_qubits = len(secret)
qc = QuantumCircuit(num_qubits + 1, num_qubits)

# Put all qubits in superposition
qc.h(range(num_qubits))
qc.x(num_qubits)
qc.h(num_qubits)

# Encode the secret number
for i, bit in enumerate(secret):
    if bit == "1":
        qc.cx(i, num_qubits)

# Undo superposition
qc.h(range(num_qubits))
qc.measure(range(num_qubits), range(num_qubits))

sim = AerSimulator()
result = sim.run(qc, shots=1).result()
answer = list(result.get_counts().keys())[0]

print(f"Secret was:  {secret}")
print(f"Quantum got: {answer[::-1]}")