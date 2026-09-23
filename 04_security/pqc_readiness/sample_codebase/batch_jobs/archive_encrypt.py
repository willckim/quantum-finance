"""SampleCo Finance -- encrypts closed-period archives before cold storage (synthetic).

Already migrated: the archive key is wrapped with a hybrid X25519MLKEM768 exchange and
the archive itself is sealed with AES-256-GCM.
"""

ARCHIVE_POLICY = {
    "key_exchange": "X25519MLKEM768",
    "kem": "ML-KEM-768",
    "signature": "ML-DSA-65",
    "cipher": "AES-256-GCM",
    "digest": "SHA-384",
    "library_kem_id": "ml_kem_768",
    "library_sig_id": "ml_dsa_65",
}
