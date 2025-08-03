import matplotlib
import numpy
import yaml
import networkx
import pandas
import tqdm
import scipy
import sklearn
import quri_parts
import qiskit
import qulacs
import skqulacs
import pyqubo
import openjij
import cirq
import pennylane
import openfermion
import time

from quri_parts_oqtopus.backend.sampling import OqtopusSamplingBackend, OqtopusConfig
from quri_parts.circuit import QuantumCircuit

for i in range(3):
  time.sleep(1)
  print(f"## Start iteration {i} ##")
  try:
    circuit = QuantumCircuit(2)
    circuit.add_H_gate(0)
    circuit.add_X_gate(1)
    circuit.add_CNOT_gate(0, 1)
    circuit.add_RY_gate(1, 0.1*i)
    transpiler_info = {
      "transpiler_lib": None
    }
    job = OqtopusSamplingBackend().sample(circuit, shots=10*i+100, name="test circuit", device_id="", transpiler_info=transpiler_info)
    print(job)
    result = job.result()
    print("#### Result:")
    print(result.counts)

  except Exception as e:
    import traceback
    print(traceback.format_exc())
    print("#### FAILED")

print("## Finish ##")
