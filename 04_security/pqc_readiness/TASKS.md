# PQC readiness toolkit: tasks

## Done
- [x] Part 1: toy Shor's algorithm factors 15 (a = 7, r = 4) with classical post-processing
- [x] Part 2: Mosca calculator over cited retention periods and three planning windows
- [x] Part 3: read-only crypto scanner, synthetic sample codebase, line-exact answer key
- [x] Part 3b: validation mode (precision and recall against a hand-marked CSV)
- [x] Part 4: X25519 vs ML-KEM-768 vs hybrid X25519MLKEM768, sizes and timings
- [x] Part 5: readiness assessment (Condition / Criteria / Cause / Effect / Recommendation),
      scope statement, defaults note, Limitations section, exposure chart
- [x] Part 6: vendor questionnaire, tracker template, five fictional vendors, computed ratings
- [x] All inputs in pqc_config.yaml, validated on load (broken and valid variants tested)
- [x] Every citation re-verified; drafts labeled at every citation; PCI DSS 12.3.3 verified
      from the PCI SSC's official SAQ D
- [x] Location-neutral sources (UCC 2-725 for contracts); no location hints in the repo
- [x] 56 self-checks; two independent code reviews, every reported mutant now caught
- [x] Part 7: migration register (asset register join, Mosca exposure, stated priority
      rule, cited remediation, per-row evidence) as xlsx and CSV
- [x] Part 8: CycloneDX 1.7 CBOM, validated against the vendored 1.7.2 schema
- [x] 92 self-checks; register and CBOM guards mutation-tested; a third independent review
      fixed formula injection, literal path matching, CBOM names, stable row ids

## Next
- [ ] Grade the scanner against a second, independent corpus (a public project with a
      published crypto inventory) so recall is evidenced beyond the planted sample
- [ ] Add an evidence column to the vendor tracker (what was received, when)
- [ ] Let the scanner record key usage (key establishment vs signature) so RSA and
      curve findings can carry a CycloneDX algorithm family instead of "unknown"
- [ ] Add a signature comparison (ECDSA vs ML-DSA-65) once key exchange is settled
- [ ] Sensitivity table: minimum migration time at which each data type becomes exposed
- [ ] Network-side inventory: read-only TLS handshake probe of a local test server
