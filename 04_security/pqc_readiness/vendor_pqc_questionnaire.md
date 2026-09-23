# Vendor Post-Quantum Cryptography Questionnaire

Send to every vendor that stores, processes or transmits the organization's sensitive
data. Record the answers in the vendor tracker (`vendor_tracker_template.csv`); the
tracker columns map to Q1 (pqc_roadmap), Q3 (algorithms_stated), Q4 (pqc_target_year)
and Q5 (hybrid_in_transit).

**Why we are asking.** Data encrypted with today's public-key cryptography (RSA and
elliptic-curve) can be recorded now and decrypted once a cryptographically relevant
quantum computer exists ("harvest now, decrypt later"). NIST published the replacement
standards in August 2024 (FIPS 203, 204 and 205). We are asking each vendor how and when
the data it holds for us will be protected by them.

| # | Question | What a strong answer includes |
|---|---|---|
| Q1 | Do you have a documented post-quantum cryptography (PQC) migration roadmap? Please share it or a summary. | A dated plan with an accountable owner, covering products and infrastructure. |
| Q2 | Have you inventoried where your product and infrastructure use public-key cryptography (RSA, elliptic-curve, Diffie-Hellman)? | A maintained inventory, reviewed at least annually. |
| Q3 | Which post-quantum algorithms and standards do you use or plan to use (for example ML-KEM / FIPS 203, ML-DSA / FIPS 204, SLH-DSA / FIPS 205)? Are the implementations validated under FIPS 140-3? | NIST-standardized algorithms, named, with validated implementations. |
| Q4 | By what dates will (a) key exchange and (b) digital signatures protecting our data use post-quantum algorithms? | Specific years for each, with key exchange first. |
| Q5 | Do you support hybrid key exchange (classical plus post-quantum, for example X25519MLKEM768 per RFC 10024) on connections that carry our data? Since when, or by when? | Enabled today, or a date, on every endpoint that carries our data. |
| Q6 | How is our data protected in transit (protocols, TLS versions, key-exchange groups), across web, API, file transfer and email? | TLS 1.3 everywhere, with hybrid groups where supported; no legacy protocols. |
| Q7 | How is our data protected at rest: which algorithm and key length, and how are data-encryption keys wrapped (RSA, or a symmetric key service)? | AES-256 data keys wrapped by a symmetric key service, or a PQC wrap plan. |
| Q8 | Who manages the keys, and what is your plan for post-quantum certificates and PKI? | Named key owner, HSM-backed keys, and a certificate migration plan. |
| Q9 | Can you change cryptographic algorithms by configuration, without a new product release? | Yes, with a tested procedure. |
| Q10 | Do your subprocessors (cloud, CDN, HSM and other providers) have PQC roadmaps, and how do you track them? | Tracked in your own vendor management, with dates. |
| Q11 | Do any connections carrying our data still accept TLS 1.0 or 1.1, SHA-1 or 3DES? If so, when will they be removed? | No; or a removal date within 90 days. |
| Q12 | Will you commit to these timelines in our contract and notify us of changes? What evidence can you provide (test reports, attestations, independent assessments)? | Contractual commitment plus third-party evidence. |

Answers are recorded as the vendor's statements. Where possible, please attach evidence
(Q12).
