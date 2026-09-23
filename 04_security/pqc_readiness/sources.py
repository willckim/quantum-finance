"""Citation registry: every external number in this toolkit, with where it came from.

Each figure below was checked against the named source on 2026-09-23 (web research, not
memory). Modules import these values instead of retyping them, so a number and its
citation cannot drift apart, and the audit report prints this list as its references.

STATUS: every source carries one of three labels, and the report must repeat the label
wherever the source is cited (run_all.py checks this paragraph by paragraph):
  verified    the claim was read on the page at the URL
  draft       the document is a public draft; cited as the stated direction of travel,
              not as a final requirement (NIST IR 8547, NIST SP 800-131A Rev. 3)
  unverified  the primary text could not be opened; the claim rests on secondary sources
"""

from __future__ import annotations

from dataclasses import dataclass


VERIFIED, DRAFT, UNVERIFIED = "verified", "draft", "unverified"


@dataclass(frozen=True)
class Source:
    key: str
    citation: str
    url: str
    status: str = VERIFIED


_ALL = (
    # --- record retention (Part 2 shelf lives) -----------------------------------------
    Source("SEC-2-06", "SEC Regulation S-X Rule 2-06, 17 CFR 210.2-06(a): auditors retain "
           "audit and review records for seven years after concluding the audit",
           "https://www.law.cornell.edu/cfr/text/17/210.2-06"),
    Source("PCAOB-1215", "PCAOB AS 1215.14, Audit Documentation: retain for seven years "
           "from the report release date",
           "https://pcaobus.org/oversight/standards/auditing-standards/details/AS1215"),
    Source("IRS-RECORDS", "IRS, How long should I keep records? (3 years general, 6 years "
           "if income under-reported by more than 25%, 7 years for bad-debt or worthless-"
           "securities claims; property records until the limitations period for the year "
           "of disposal expires)",
           "https://www.irs.gov/businesses/small-businesses-self-employed/"
           "how-long-should-i-keep-records"),
    Source("IRC-6501", "26 U.S.C. 6501, limitations on assessment and collection",
           "https://www.law.cornell.edu/uscode/text/26/6501"),
    Source("IRS-EMPTAX", "IRS, Employment tax recordkeeping: keep employment tax records "
           "at least four years after the fourth-quarter filing",
           "https://www.irs.gov/businesses/small-businesses-self-employed/"
           "employment-tax-recordkeeping"),
    Source("FLSA-516", "29 CFR 516.5: payroll records preserved for at least 3 years",
           "https://www.law.cornell.edu/cfr/text/29/516.5"),
    Source("FLSA-516-6", "29 CFR 516.6: supplementary payroll records preserved for at least "
           "2 years", "https://www.law.cornell.edu/cfr/text/29/516.6"),
    Source("BSA-1010-430", "Bank Secrecy Act recordkeeping, 31 CFR 1010.430(d): records "
           "retained for five years", "https://www.law.cornell.edu/cfr/text/31/1010.430"),
    Source("PCI-FAQ-RETENTION", "PCI SSC FAQ: PCI DSS does not define minimum or maximum "
           "times for storing cardholder data; Requirement 3.2.1 limits storage to that "
           "which is necessary for legal, regulatory, and/or business purposes",
           "https://www.pcisecuritystandards.org/faq/articles/Frequently_Asked_Question/"
           "what-is-the-maximum-period-of-time-that-cardholder-data-can-be-stored/"),
    Source("NIST-800-57", "NIST SP 800-57 Part 1 Rev. 5 (2020), Section 5.3.6 and Table 1: "
           "private authentication keys, cryptoperiod of one to two years",
           "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-57pt1r5.pdf"),
    Source("NIST-800-63B", "NIST SP 800-63B-4, Section 3.1.1.2: verifiers shall not require "
           "periodic password changes (only on evidence of compromise)",
           "https://pages.nist.gov/800-63-4/sp800-63b.html"),
    Source("UCC-2-725", "Uniform Commercial Code 2-725(1): an action for breach of a "
           "contract for sale of goods must be commenced within four years; the parties may "
           "shorten the period to not less than one year but may not extend it",
           "https://www.law.cornell.edu/ucc/2/2-725"),
    Source("IRC-197", "26 U.S.C. 197(a): acquired intangibles, including goodwill, amortized "
           "ratably over 15 years", "https://www.law.cornell.edu/uscode/text/26/197"),
    Source("ERISA-107", "ERISA section 107, 29 U.S.C. 1027: benefit-plan records kept not "
           "less than six years after filing", "https://www.law.cornell.edu/uscode/text/29/1027"),
    Source("OSHA-1910-1020", "29 CFR 1910.1020: employee medical records kept for the "
           "duration of employment plus 30 years",
           "https://www.law.cornell.edu/cfr/text/29/1910.1020"),
    # --- quantum threat timing (Part 2 scenarios) ---------------------------------------
    Source("GRI-2025", "Mosca and Piani, Quantum Threat Timeline Report 2025, Global Risk "
           "Institute (published March 2026; survey of 26 experts in 2025): a CRQC able to "
           "factor a 2048-bit number in under 24 hours is 28-49% likely within 10 years "
           "('by roughly 2035') and 51-70% likely within 15 years, counted from 2025",
           "https://globalriskinstitute.org/publication/quantum-threat-timeline-report-2025b/"),
    Source("MOSCA-2015", "Mosca, Cybersecurity in an era with quantum computers: will we be "
           "ready? (IACR ePrint 2015/1075): a 1/2 chance of breaking RSA-2048 by 2031",
           "https://eprint.iacr.org/2015/1075"),
    Source("NIST-IR-8547", "NIST IR 8547 (initial public draft, Nov 2024): 112-bit RSA and "
           "ECC deprecated after 2030 and disallowed after 2035; standardization to full "
           "integration 'can take 10 to 20 years'",
           "https://csrc.nist.gov/pubs/ir/8547/ipd", DRAFT),
    Source("EO-14412", "Executive Order 14412, Securing the Nation Against Advanced "
           "Cryptographic Attacks (22 June 2026): for federal high-value assets and "
           "high-impact systems (national security systems excluded), PQC key establishment "
           "by 31 Dec 2030 and PQC signatures by 31 Dec 2031, via OMB guidance; directs a "
           "proposed FAR rule requiring covered contractors to comply with PQC FIPS by "
           "31 Dec 2030",
           "https://www.whitehouse.gov/presidential-actions/2026/06/"
           "securing-the-nation-against-advanced-cryptographic-attacks/"),
    Source("NCSC-TIMELINE", "UK NCSC, Timelines for migration to post-quantum cryptography "
           "(March 2025): discovery by 2028 (including dependencies on suppliers: "
           "'communicate your needs to your suppliers'), priority migration by 2031, "
           "complete by 2035; '10 years is a sufficient period' for migration",
           "https://www.ncsc.gov.uk/guidance/pqc-migration-timelines"),
    Source("NIST-SHA1", "NIST, Transitioning away from SHA-1 (Dec 2022): SHA-1 deprecated in "
           "2011, to be phased out by 31 Dec 2030 (about 19 years, by our arithmetic)",
           "https://csrc.nist.gov/news/2022/nist-transitioning-away-from-sha-1-for-all-apps"),
    Source("CISA-HNDL", "CISA, NSA and NIST, Quantum-Readiness: Migration to Post-Quantum "
           "Cryptography (Aug 2023): adversaries may 'catch now, break later' data with a "
           "long secrecy lifetime",
           "https://www.cisa.gov/sites/default/files/2023-08/"
           "Quantum%20Readiness_Final_CLEAR_508c%20%283%29.pdf"),
    Source("CISA-VENDORS", "CISA, NSA and NIST, Quantum-Readiness factsheet (Aug 2023), "
           "'Discuss post-quantum roadmaps with technology vendors': organizations are "
           "encouraged to engage their technology vendors to learn about their "
           "quantum-readiness roadmaps, and to plan for changes to existing and future "
           "contracts", "https://www.cisa.gov/sites/default/files/2023-08/"
           "Quantum%20Readiness_Final_CLEAR_508c%20%283%29.pdf"),
    Source("G7-CEG-2024-THIRD-PARTIES", "G7 Cyber Expert Group statement on planning for the "
           "opportunities and risks of quantum computing (Sept 2024): financial entities "
           "'may consider outreach to vendors, third parties' and inventory quantum-"
           "vulnerable technology in their organizations and 'key third parties on which "
           "they are dependent'", "https://home.treasury.gov/system/files/136/"
           "G7-CYBER-EXPERT-GROUP-STATEMENT-PLANNING-OPPORTUNITIES-RISKS-QUANTUM-COMPUTING.pdf"),
    Source("G7-CEG-2024", "G7 Cyber Expert Group, statement on planning for the quantum "
           "opportunities and risks (Sept 2024): financial institutions should 'develop a "
           "plan for mitigating quantum computing risks'",
           "https://home.treasury.gov/news/press-releases/jy2609"),
    # --- standards and control criteria (Parts 3-5) ------------------------------------
    Source("FIPS-203", "NIST FIPS 203, Module-Lattice-Based Key-Encapsulation Mechanism "
           "Standard (Aug 2024), Table 3 sizes; seed (d, z) storage; implicit rejection",
           "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf"),
    Source("FIPS-204-205", "NIST FIPS 204 (ML-DSA) and FIPS 205 (SLH-DSA), approved "
           "13 Aug 2024", "https://csrc.nist.gov/news/2024/postquantum-cryptography-fips-approved"),
    Source("RFC-10024", "RFC 10024, Post-Quantum Traditional (PQ/T) Hybrid Key Agreement "
           "Mechanisms for TLS 1.3 (Aug 2026): X25519MLKEM768, codepoint 0x11EC, ML-KEM part "
           "first; designed to stay secure while at least one component algorithm is",
           "https://www.rfc-editor.org/rfc/rfc10024.txt"),
    Source("RFC-7748", "RFC 7748, Elliptic Curves for Security (X25519: 32-byte keys)",
           "https://www.rfc-editor.org/rfc/rfc7748"),
    Source("RFC-8996", "RFC 8996 (BCP 195, March 2021): TLS 1.0 and 1.1 formally deprecated",
           "https://www.rfc-editor.org/rfc/rfc8996"),
    Source("NIST-800-131A", "NIST SP 800-131A Rev. 3 (initial public draft, Oct 2024): "
           "TDEA (3DES) disallowed as of 2024; SHA-1 disallowed after 31 Dec 2030",
           "https://csrc.nist.gov/pubs/sp/800/131/a/r3/ipd", DRAFT),
    Source("PCI-12-3-3", "PCI DSS v4.0 Requirement 12.3.3, as reproduced in the PCI SSC's "
           "official SAQ D for Merchants (the standard's own PDF could not be retrieved): "
           "cipher suites and protocols in use are documented and reviewed at least once "
           "every 12 months, including an up-to-date inventory, monitoring of their "
           "continued viability, and 'a documented strategy to respond to anticipated "
           "changes in cryptographic vulnerabilities'; a best practice until 31 March 2025, "
           "required thereafter", "https://listings.pcisecuritystandards.org/documents/"
           "PCI-DSS-v4-0-SAQ-D-Merchant.docx"),
    Source("NCSC-HYBRID", "UK NCSC, Next steps in preparing for post-quantum cryptography: "
           "'If a PQ/T hybrid scheme is chosen, the NCSC recommends it is used as an interim "
           "measure', within a framework that allows a later move to PQC-only",
           "https://www.ncsc.gov.uk/whitepaper/next-steps-preparing-for-post-quantum-cryptography"),
    # --- Shor resource estimates (Part 1) ----------------------------------------------
    Source("GIDNEY-2025", "Gidney, How to factor 2048 bit RSA integers with less than a "
           "million noisy qubits (arXiv 2505.15917, May 2025): about 1,400 logical qubits "
           "(1,399 in its cost table), under a million physical qubits at 0.1% gate error, "
           "under a week", "https://arxiv.org/abs/2505.15917"),
    Source("GIDNEY-EKERA-2019", "Gidney and Ekera, How to factor 2048 bit RSA integers in 8 "
           "hours using 20 million noisy qubits (arXiv 1905.09749, 2019)",
           "https://arxiv.org/abs/1905.09749"),
    Source("BEAUREGARD-2003", "Beauregard, Circuit for Shor's algorithm using 2n+3 qubits "
           "(arXiv quant-ph/0205095): 2n + 3 qubits, which is 4,099 for n = 2048 by our "
           "arithmetic",
           "https://arxiv.org/abs/quant-ph/0205095"),
)

SOURCES: dict[str, Source] = {s.key: s for s in _ALL}


@dataclass(frozen=True)
class Estimate:
    sentence: str
    source_keys: tuple[str, ...]


RSA_2048_ESTIMATE = Estimate(
    "RSA-2048 would need roughly 1,400 error-corrected logical qubits, backed by just under "
    "a million physical qubits at a 0.1% error rate, running for under a week (Gidney, "
    "2025). Six years earlier the estimate was 20 million physical qubits (Gidney and "
    "Ekera, 2019): the threat moves with algorithm research, not only with hardware.",
    ("GIDNEY-2025", "GIDNEY-EKERA-2019", "BEAUREGARD-2003"),
)

# FIPS 203, Table 3 (ML-KEM-768) plus the 64-byte seed (d, z) of Algorithm 19.
FIPS_203_SIZES = {"encapsulation_key": 1184, "decapsulation_key": 2400, "ciphertext": 1088,
                  "shared_secret": 32, "seed": 64}
