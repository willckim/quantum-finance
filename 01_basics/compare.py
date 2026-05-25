from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
import time

secret = "11010011101010001110110010101100111010110011101010001101101011001010011101001110101000111011001010110011"

num_qubits = len(secret)
print(f"Secret number: {secret}")
print(f"Length: {num_qubits} bits\n")

# --- CLASSICAL APPROACH ---
# Has to guess one bit at a time
start = time.time()
classical_guesses = 0
classical_answer = ""

for i in range(num_qubits):
    # Each query only reveals one bit
    classical_answer += secret[i]
    classical_guesses += 1

classical_time = time.time() - start
print(f"CLASSICAL")
print(f"  Answer:  {classical_answer}")
print(f"  Queries: {classical_guesses}")
print(f"  Time:    {classical_time:.6f}s\n")

# --- QUANTUM APPROACH ---
# Gets it in one shot
start = time.time()

qc = QuantumCircuit(num_qubits + 1, num_qubits)
qc.h(range(num_qubits))
qc.x(num_qubits)
qc.h(num_qubits)

for i, bit in enumerate(secret):
    if bit == "1":
        qc.cx(i, num_qubits)

qc.h(range(num_qubits))
qc.measure(range(num_qubits), range(num_qubits))

sim = AerSimulator()
result = sim.run(qc, shots=1).result()
answer = list(result.get_counts().keys())[0][::-1]

quantum_time = time.time() - start
print(f"QUANTUM")
print(f"  Answer:  {answer}")
print(f"  Queries: 1")
print(f"  Time:    {quantum_time:.6f}s\n")

# --- COMPARISON ---
print(f"Match: {answer == secret}")
print(f"Classical needed {classical_guesses}x more queries")