from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

qc = QuantumCircuit(3, 3)

# Prepare qubit 0 in a state we want to teleport
qc.h(0)
qc.t(0)

# Create entangled pair between qubit 1 and 2
qc.h(1)
qc.cx(1, 2)

# Teleportation protocol
qc.cx(0, 1)
qc.h(0)
qc.measure([0, 1], [0, 1])

# Apply corrections based on measurements
qc.cx(1, 2)
qc.cz(0, 2)
qc.measure(2, 2)

sim = AerSimulator()
result = sim.run(qc, shots=1000).result()
print(result.get_counts())