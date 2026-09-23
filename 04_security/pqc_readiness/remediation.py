"""Recommended remediation for each kind of finding, with the standard it comes from.

These are STARTING POINTS FOR ASSESSMENT AND MIGRATION PLANNING, not fixes. The scanner
sees an algorithm's name in text, not how a system uses it, so each entry says what to
confirm first. Whether a replacement is right for a given system depends on its
protocols, vendor support and performance limits, which only its owners can judge.
Every source key resolves in sources.py; drafts are labeled wherever they are cited.
"""

from __future__ import annotations

from dataclasses import dataclass

from common import Check
from crypto_scanner import RULES
from sources import SOURCES

PLANNING_NOTE = ("Recommended starting point for assessment and migration planning; "
                 "confirm against the system's requirements and vendor support first.")


@dataclass(frozen=True)
class Remediation:
    action: str
    source_keys: tuple[str, ...]


_KEY_EXCHANGE = ("plan a move to hybrid X25519MLKEM768 (ML-KEM-768 combined with X25519, the "
                 "TLS 1.3 construction in RFC 10024)")
_SIGNATURES = ("plan a move to ML-DSA (FIPS 204), or SLH-DSA (FIPS 205) where hash-based "
               "signatures fit")

REMEDIATIONS: dict[str, Remediation] = {
    "QV-RSA": Remediation(
        "Assess whether RSA is used for key establishment or for signatures. For key "
        f"establishment, {_KEY_EXCHANGE}; for signatures, {_SIGNATURES}.",
        ("FIPS-203", "RFC-10024", "FIPS-204-205", "NIST-IR-8547")),
    "QV-ECC": Remediation(
        "Assess whether the elliptic-curve use is key agreement (ECDH, X25519) or "
        f"signatures (ECDSA, EdDSA). For key agreement, {_KEY_EXCHANGE}; for signatures, "
        f"{_SIGNATURES}.",
        ("FIPS-203", "RFC-10024", "FIPS-204-205", "NIST-IR-8547")),
    "QV-DH": Remediation(
        f"Assess where finite-field Diffie-Hellman establishes keys, then {_KEY_EXCHANGE}.",
        ("FIPS-203", "RFC-10024", "NIST-IR-8547")),
    "QV-DSA": Remediation(
        f"Assess what DSA signs and who verifies it, then {_SIGNATURES}.",
        ("FIPS-204-205", "NIST-IR-8547")),
    "QV-KEYSIZE": Remediation(
        "A larger key does not protect against Shor's algorithm. Assess which algorithm "
        "this key size belongs to and plan its post-quantum replacement (key establishment "
        "on ML-KEM, signatures on ML-DSA); move key parameters into central configuration.",
        ("FIPS-203", "FIPS-204-205")),
    "CW-KEYSIZE": Remediation(
        "Keys under 2048 bits fall below 112-bit security. Plan an interim move to at least "
        "2048-bit keys and a post-quantum replacement as the target.",
        ("NIST-800-57-STRENGTH", "FIPS-203", "FIPS-204-205")),
    "CW-MD5": Remediation(
        "Assess what the digest protects (integrity, signatures, or a non-security use such "
        "as de-duplication, which changes urgency), then plan SHA-256 or stronger (SHA-2) or "
        "SHA-3.",
        ("FIPS-180-4", "FIPS-202")),
    "CW-SHA1": Remediation(
        "Assess what the digest protects, then plan SHA-256 or stronger (SHA-2) or SHA-3. "
        "In a TLS or SSH cipher suite, this means retiring the suite.",
        ("FIPS-180-4", "FIPS-202", "NIST-SHA1", "NIST-800-131A")),
    "CW-CIPHER": Remediation(
        "Plan replacement of DES, 3DES or RC4 with AES-256 in an authenticated mode; assess "
        "any stored data encrypted under the old cipher for re-encryption.",
        ("FIPS-197", "NIST-800-131A")),
    "CW-TLS-LEGACY": Remediation(
        "Plan removal of SSL and TLS 1.0/1.1 in favour of TLS 1.3, then enable hybrid key "
        "exchange where both ends support it; confirm partner compatibility first.",
        ("RFC-8996", "RFC-8446", "RFC-10024")),
    "PQ-HYBRID": Remediation(
        "No post-quantum change indicated if this is a standardized group (X25519MLKEM768 or "
        "an ML-KEM SecP group); the pre-standard X25519Kyber768 should be planned onto "
        "X25519MLKEM768. Confirm the implementation is a validated build.",
        ("RFC-10024", "FIPS-203")),
    "PQ-KEM": Remediation(
        "No post-quantum change indicated for the algorithm. Confirm the parameter set and "
        "that the implementation is a validated build.", ("FIPS-203",)),
    "PQ-SIGNATURE": Remediation(
        "No post-quantum change indicated for the algorithm. Confirm the parameter set and "
        "that the implementation is a validated build.", ("FIPS-204-205",)),
    "PQ-SYMMETRIC": Remediation(
        "No post-quantum change indicated for the key length. Confirm an authenticated mode "
        "(for example GCM, not ECB) and that the key exchange delivering its keys is itself "
        "post-quantum.", ("FIPS-197",)),
    "PQ-HASH": Remediation(
        "No post-quantum change indicated. Confirm how the digest is used and keep it in the "
        "inventory.", ("FIPS-180-4", "FIPS-202")),
}


def remediation_for(rule_id: str) -> Remediation:
    return REMEDIATIONS[rule_id]


def remediation_checks() -> list[Check]:
    rule_ids = {r.rule_id for r in RULES} | {"QV-KEYSIZE"}
    missing = sorted(rule_ids - REMEDIATIONS.keys())
    extra = sorted(REMEDIATIONS.keys() - rule_ids)
    unknown = sorted({k for r in REMEDIATIONS.values() for k in r.source_keys} - SOURCES.keys())
    uncited = [k for k, r in REMEDIATIONS.items() if not r.source_keys]
    return [
        Check("Remediation: every scanner rule has exactly one mapped remediation",
              not missing and not extra, f"{len(rule_ids)} rules"
              + (f"; missing {missing}" if missing else "") + (f"; extra {extra}" if extra else "")),
        Check("Remediation: every mapping cites sources that exist in the registry",
              not unknown and not uncited,
              f"unknown {unknown}; uncited {uncited}" if unknown or uncited else ""),
    ]
