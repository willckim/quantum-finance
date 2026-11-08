# Fraud detection: classical vs quantum kernel

## Done
- [x] Dataset chosen and license checked: ULB/Worldline credit card fraud (ODbL database, DbCL contents). IBM AMLworld (CDLA-Sharing-1.0) ruled out at 41.6 GB.
- [x] `download_data.py`: OpenML mirror, MD5 verified, raw file gitignored
- [x] Full-data baselines: logistic regression, gradient boosting
- [x] Fair comparison on a 400-row balanced subsample, 6 PCA features: LR, GB, RBF SVM, quantum kernel SVM, equal tuning budgets
- [x] Metrics: PR-AUC, recall at 0.1% FPR, confusion matrix, cost view with sensitivity table, paired bootstrap intervals, 5-draw stability
- [x] Quantum kernel checked against qiskit's circuit and against shot-measured Aer circuits; shot-noise stress test
- [x] Outputs: PR chart, report, summary CSV, run log; self-checks; mutation tests

## Ideas, not started
- [ ] Time-based split (train on day 1, test on day 2) to mimic live monitoring
- [ ] Repeat at 4 and 8 qubits to see whether more features change the ranking
- [ ] Trainable or projected quantum kernels under the same tuning budget
- [ ] AML version on a laptop-sized synthetic transaction graph
