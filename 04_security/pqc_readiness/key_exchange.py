"""Part 4 -- Classical vs post-quantum key exchange: X25519, ML-KEM-768, and the hybrid.

WHAT A KEY EXCHANGE DOES
------------------------
Before two computers can encrypt anything, they must agree on a shared secret key over
a network anyone can listen to. Today that is almost always X25519, an elliptic-curve
Diffie-Hellman exchange: fast and tiny (32-byte messages), and completely broken by
Shor's algorithm, because its security rests on the elliptic-curve discrete logarithm.

This is the part of the connection that "harvest now, decrypt later" targets. An attacker
who records the handshake today can, once a quantum computer exists, recover the X25519
secret from the recorded public values, derive the session key, and decrypt every byte
that was recorded. Signatures do not have that problem (a forged signature must be forged
BEFORE it is checked), which is why migrations do key exchange first.

ML-KEM-768 (NIST FIPS 203, August 2024) replaces it. It is a key-encapsulation mechanism
built on the Module Learning With Errors lattice problem, which has no known quantum
shortcut. The trade: messages about 36 times bigger, while speed stays in the same range.

WHY REAL MIGRATIONS USE A HYBRID (the plain-language version)
-------------------------------------------------------------
A hybrid runs BOTH exchanges in the same handshake and feeds both secrets into the key.
An attacker has to break both to read the traffic.

  * If ML-KEM turns out to have a flaw (it is new; lattice schemes have had far less
    public attack time than elliptic curves, and a finalist of the same NIST process,
    SIKE, was broken on a laptop in 2022), X25519 still protects today's traffic.
  * If a quantum computer arrives, ML-KEM still protects the recording.

It is a seatbelt plus an airbag. The cost is 32 extra bytes each way and one extra
X25519 operation, which is negligible. The X25519MLKEM768 group is the default in major
browsers and large content-delivery networks, and RFC 10024 standardizes it for TLS 1.3.
The UK NCSC treats hybrid as an interim step on the way to PQC-only, so it is a
reasonable answer to "what are you migrating to first?", not the end state.

HOW THE HYBRID IS BUILT HERE
----------------------------
It follows the TLS construction in RFC 10024 (August 2026; group X25519MLKEM768, 0x11EC):
the client's key share is the ML-KEM encapsulation key followed by its X25519 public key,
the server's reply is the ML-KEM ciphertext followed by its X25519 public key, and the
shared secret is the ML-KEM secret followed by the X25519 secret (64 bytes). TLS 1.3's
key schedule then runs that through HKDF. No new cryptography is invented in this file.

LIBRARY CHOICE (documented substitution check)
----------------------------------------------
pyca/cryptography 48.0.0 was already installed and exposes ML-KEM-768 natively through
its bundled OpenSSL, so nothing new was installed. liboqs-python would need a CMake and C
compiler toolchain (neither present), and pure-Python kyber-py would be orders of
magnitude slower, distorting the timing comparison.

GOTCHA: MLKEM768PrivateKey.private_bytes_raw() returns 64 bytes, not the 2,400-byte
decapsulation key in the FIPS 203 table. It is the seed (d, z) that the key expands from.
FIPS 203 permits storing the seed instead of the expanded key, and it is 37 times smaller.

Timings are single-machine medians. They show the order of magnitude, not a benchmark.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Callable

from cryptography.hazmat.primitives.asymmetric import mlkem, x25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from common import KEM_TIMING_ROUNDS, Check, section
from sources import FIPS_203_SIZES


# ======================================================================================
# THREE KEY EXCHANGES WITH ONE INTERFACE: keygen, encapsulate, decapsulate
# ======================================================================================
# A Diffie-Hellman exchange can be phrased as a KEM (this is how HPKE's DHKEM works): the
# "ciphertext" is a fresh ephemeral public key, and "encapsulating" is doing the exchange
# with it. Using one interface lets the three be measured side by side.

def _x25519_pub(key: x25519.X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


class X25519Kem:
    name = "X25519 (classical)"

    @staticmethod
    def keygen() -> tuple[bytes, x25519.X25519PrivateKey]:
        sk = x25519.X25519PrivateKey.generate()
        return _x25519_pub(sk), sk

    @staticmethod
    def encapsulate(pk: bytes) -> tuple[bytes, bytes]:
        ephemeral = x25519.X25519PrivateKey.generate()
        secret = ephemeral.exchange(x25519.X25519PublicKey.from_public_bytes(pk))
        return secret, _x25519_pub(ephemeral)

    @staticmethod
    def decapsulate(sk: x25519.X25519PrivateKey, ct: bytes) -> bytes:
        return sk.exchange(x25519.X25519PublicKey.from_public_bytes(ct))


class MlKem768:
    name = "ML-KEM-768 (FIPS 203)"

    @staticmethod
    def keygen() -> tuple[bytes, mlkem.MLKEM768PrivateKey]:
        sk = mlkem.MLKEM768PrivateKey.generate()
        return sk.public_key().public_bytes_raw(), sk

    @staticmethod
    def encapsulate(pk: bytes) -> tuple[bytes, bytes]:
        return mlkem.MLKEM768PublicKey.from_public_bytes(pk).encapsulate()

    @staticmethod
    def decapsulate(sk: mlkem.MLKEM768PrivateKey, ct: bytes) -> bytes:
        return sk.decapsulate(ct)


class HybridX25519MlKem768:
    """X25519MLKEM768: ML-KEM part first, X25519 part second, in every message."""

    name = "Hybrid X25519MLKEM768"
    SPLIT_PK = FIPS_203_SIZES["encapsulation_key"]
    SPLIT_CT = FIPS_203_SIZES["ciphertext"]

    @classmethod
    def keygen(cls):
        pk_m, sk_m = MlKem768.keygen()
        pk_x, sk_x = X25519Kem.keygen()
        return pk_m + pk_x, (sk_m, sk_x)

    @classmethod
    def encapsulate(cls, pk: bytes) -> tuple[bytes, bytes]:
        ss_m, ct_m = MlKem768.encapsulate(pk[:cls.SPLIT_PK])
        ss_x, ct_x = X25519Kem.encapsulate(pk[cls.SPLIT_PK:])
        return ss_m + ss_x, ct_m + ct_x

    @classmethod
    def decapsulate(cls, sk, ct: bytes) -> bytes:
        sk_m, sk_x = sk
        return (MlKem768.decapsulate(sk_m, ct[:cls.SPLIT_CT])
                + X25519Kem.decapsulate(sk_x, ct[cls.SPLIT_CT:]))


SCHEMES = (X25519Kem, MlKem768, HybridX25519MlKem768)


# ======================================================================================
# MEASUREMENT
# ======================================================================================

@dataclass(frozen=True)
class KemProfile:
    name: str
    public_key_bytes: int
    ciphertext_bytes: int
    shared_secret_bytes: int
    keygen_us: float
    encap_us: float
    decap_us: float
    quantum_safe: str


def _median_us(fn: Callable[[], object], rounds: int) -> float:
    samples = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - start)
    return statistics.median(samples) / 1_000


def profile(scheme, rounds: int = KEM_TIMING_ROUNDS) -> KemProfile:
    pk, sk = scheme.keygen()
    ss, ct = scheme.encapsulate(pk)
    return KemProfile(
        scheme.name, len(pk), len(ct), len(ss),
        _median_us(scheme.keygen, rounds),
        _median_us(lambda: scheme.encapsulate(pk), rounds),
        _median_us(lambda: scheme.decapsulate(sk, ct), rounds),
        {X25519Kem: "no (Shor)", MlKem768: "yes", HybridX25519MlKem768: "yes (either suffices)"}
        [scheme])


def _size_checks(x: KemProfile, m: KemProfile, h: KemProfile) -> list[Check]:
    """Expected sizes come from the standards (FIPS 203 Table 3, RFC 7748), not the library."""
    fips = (FIPS_203_SIZES["encapsulation_key"], FIPS_203_SIZES["ciphertext"],
            FIPS_203_SIZES["shared_secret"])
    return [
        Check("KEM: ML-KEM-768 sizes match the FIPS 203 parameter table",
              (m.public_key_bytes, m.ciphertext_bytes, m.shared_secret_bytes) == fips,
              f"{m.public_key_bytes}/{m.ciphertext_bytes}/{m.shared_secret_bytes} B"),
        Check("KEM: X25519 sizes match RFC 7748 (32-byte keys and secret)",
              (x.public_key_bytes, x.ciphertext_bytes, x.shared_secret_bytes) == (32, 32, 32)),
        Check("KEM: hybrid sizes are ML-KEM + X25519 (1216 / 1120 / 64 B)",
              (h.public_key_bytes, h.ciphertext_bytes, h.shared_secret_bytes)
              == (fips[0] + 32, fips[1] + 32, 64)),
    ]


def _hybrid_layout_check() -> Check:
    """RFC 10024 byte order: ML-KEM part FIRST in the key share, ciphertext and secret.

    Sizes and round trips cannot see the order (an X25519-first hybrid has the same sizes
    and still agrees with itself), so each slice is checked against the component that
    should own it.
    """
    n_pk, n_ct = FIPS_203_SIZES["encapsulation_key"], FIPS_203_SIZES["ciphertext"]
    pk, (sk_m, sk_x) = HybridX25519MlKem768.keygen()
    ss, ct = HybridX25519MlKem768.encapsulate(pk)
    try:
        ok = (sk_m.public_key().public_bytes_raw() == pk[:n_pk]
              and _x25519_pub(sk_x) == pk[n_pk:]
              and MlKem768.decapsulate(sk_m, ct[:n_ct]) == ss[:32]
              and X25519Kem.decapsulate(sk_x, ct[n_ct:]) == ss[32:])
    except (ValueError, TypeError, AttributeError):
        ok = False
    return Check("KEM: hybrid byte order is ML-KEM first, then X25519 (RFC 10024)", ok,
                 "key share, ciphertext and shared secret each checked by slice")


def _behavior_checks(trials: int = 25) -> list[Check]:
    checks = []
    for scheme in SCHEMES:
        ok = 0
        for _ in range(trials):
            pk, sk = scheme.keygen()
            ss, ct = scheme.encapsulate(pk)
            ok += scheme.decapsulate(sk, ct) == ss
        checks.append(Check(f"KEM: {scheme.name} both sides derive the same secret",
                            ok == trials, f"{ok}/{trials} fresh key pairs"))
    # Implicit rejection: a tampered ciphertext does not raise; it yields an unrelated key,
    # so the handshake fails later without telling an attacker why.
    pk, sk = MlKem768.keygen()
    ss, ct = MlKem768.encapsulate(pk)
    rejected = MlKem768.decapsulate(sk, bytes([ct[0] ^ 1]) + ct[1:])
    checks.append(Check("KEM: tampered ML-KEM ciphertext gives a different key, no error",
                        rejected != ss and len(rejected) == 32, "implicit rejection"))
    seed = sk.private_bytes_raw()
    restored = mlkem.MLKEM768PrivateKey.from_seed_bytes(seed)
    checks.append(Check("KEM: 64-byte stored seed regenerates the same ML-KEM key",
                        len(seed) == FIPS_203_SIZES["seed"] and restored.decapsulate(ct) == ss,
                        f"{len(seed)} B seed vs {FIPS_203_SIZES['decapsulation_key']} B "
                        "expanded key"))
    return checks


def part4_key_exchange() -> tuple[list[KemProfile], list[Check]]:
    section("PART 4 -- CLASSICAL vs POST-QUANTUM KEY EXCHANGE")
    profiles = [profile(s) for s in SCHEMES]
    print(f"  Median of {KEM_TIMING_ROUNDS:,} runs per operation, this machine, one core.\n")
    print(f"  {'scheme':<24} {'public key':>10} {'ciphertext':>10} {'secret':>6} "
          f"{'keygen':>9} {'encap':>9} {'decap':>9}  quantum-safe")
    for p in profiles:
        print(f"  {p.name:<24} {p.public_key_bytes:>8} B {p.ciphertext_bytes:>8} B "
              f"{p.shared_secret_bytes:>4} B {p.keygen_us:>6.1f} us {p.encap_us:>6.1f} us "
              f"{p.decap_us:>6.1f} us  {p.quantum_safe}")
    x, m, h = profiles
    wire_x = x.public_key_bytes + x.ciphertext_bytes
    wire_h = h.public_key_bytes + h.ciphertext_bytes
    print(f"\n  Bytes on the wire per handshake: X25519 {wire_x}, hybrid {wire_h:,} "
          f"({wire_h / wire_x:.0f}x). Each key share is under 1,500 bytes on its own,")
    print("  though a full TLS ClientHello carrying one can spill into a second packet.")
    print("  The hybrid costs 64 bytes and one X25519 operation more than ML-KEM alone.")
    checks = _size_checks(x, m, h) + [_hybrid_layout_check()] + _behavior_checks()
    return profiles, checks
