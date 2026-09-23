"""SampleCo Finance -- outbound client for the synthetic card-settlement partner."""

import ssl

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa

SETTLEMENT_HOST = "settlement.example.com"


def legacy_context() -> ssl.SSLContext:
    # Partner endpoint was never upgraded, so the old protocol is pinned here.
    return ssl.SSLContext(ssl.PROTOCOL_TLSv1)


def new_client_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def sign_settlement_batch(payload: bytes, key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.sign(payload, ec.ECDSA(hashes.SHA256()))
