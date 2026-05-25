from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

qc = QuantumCircuit(2, 2)
qc.h(0)           # put first qubit in superposition
qc.cx(0, 1)       # entangle second qubit with first
qc.measure([0,1], [0,1])

sim = AerSimulator()
result = sim.run(qc, shots=1000).result()
print(result.get_counts())