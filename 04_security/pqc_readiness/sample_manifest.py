"""The answer key for the synthetic sample codebase: every finding that was planted in it.

WHERE THESE EXPECTED VALUES CAME FROM
-------------------------------------
This list was written from the sample files themselves, line by line, as each line was
planted. It is not read off scanner output. Each entry names the file, the rule that
should fire, and an ANCHOR: a piece of text that occurs on exactly one line of that
file. The check resolves each anchor to its line number by searching the file (not by
asking the scanner), so a rule that fires on the wrong line fails the check, and editing
a sample file cannot silently shift the key.

The self-check in crypto_scanner.py compares the scanner against the resolved key in
both directions: every planted (file, line, rule) must be found, and nothing else may be
found (precision, tested hardest by the negative-control files full of near-miss words).

WHAT THIS CHECK DOES AND DOES NOT GRADE
---------------------------------------
It grades the scanner against crypto usage that its author chose to plant. It cannot
grade the scanner against crypto usage nobody thought of: a construct absent from both
the sample and the rules is invisible to this check by construction. Real coverage
evidence needs a second, independent corpus (for example, a public project with a
published crypto inventory); that is listed in TASKS.md. Several idioms here (such as
SHA256withECDSA, RSAPublicKey and ml_kem_768) were added after an independent code
review found the first rule set missed them, which is exactly this blind spot at work.
"""

from pathlib import Path

# (file relative to sample_codebase, rule id, anchor text unique to the planted line)
PLANTED: tuple[tuple[str, str, str], ...] = (
    ("payments_api/tls_client.py", "QV-RSA", "import ec, rsa"),
    ("payments_api/tls_client.py", "QV-RSA", "rsa.generate_private_key"),
    ("payments_api/tls_client.py", "QV-KEYSIZE", "key_size=2048"),
    ("payments_api/tls_client.py", "QV-ECC", "ec.EllipticCurvePrivateKey"),
    ("payments_api/tls_client.py", "QV-ECC", "ec.ECDSA("),
    ("payments_api/tls_client.py", "CW-TLS-LEGACY", "PROTOCOL_TLSv1"),
    ("payments_api/webhook_verify.js", "CW-MD5", 'createHash("md5")'),
    ("payments_api/webhook_verify.js", "QV-RSA", '"RS256"'),          # RS256 JWT signature
    ("payments_api/webhook_verify.js", "QV-ECC", '"EdDSA"'),          # same line as RS256
    ("config/gateway_nginx.conf", "CW-TLS-LEGACY", "ssl_protocols TLSv1 TLSv1.1"),
    # One cipher-suite line names key exchange, authentication and MAC: four rules fire.
    ("config/gateway_nginx.conf", "QV-ECC", "ECDHE-RSA-AES128-SHA:"),
    ("config/gateway_nginx.conf", "QV-RSA", "ECDHE-RSA-AES128-SHA:"),
    ("config/gateway_nginx.conf", "QV-DH", "ECDHE-RSA-AES128-SHA:"),    # the DHE-RSA suite
    ("config/gateway_nginx.conf", "CW-SHA1", "ECDHE-RSA-AES128-SHA:"),  # -SHA = SHA-1 MAC
    ("config/gateway_nginx.conf", "QV-ECC", "ssl_ecdh_curve secp384r1"),
    ("config/gateway_nginx.conf", "QV-DH", "ssl_dhparam"),
    ("config/gateway_nginx.conf", "CW-TLS-LEGACY", "# SSL 3.0"),        # a comment: by design
    ("config/sftp_vendor_feed.yaml", "QV-RSA", "key_type: rsa"),
    ("config/sftp_vendor_feed.yaml", "CW-KEYSIZE", "key_size: 1024"),
    ("config/sftp_vendor_feed.yaml", "QV-DSA", "- ssh-dss"),
    ("config/sftp_vendor_feed.yaml", "QV-ECC", "- ecdsa-sha2-nistp256"),
    ("config/sftp_vendor_feed.yaml", "QV-ECC", "- curve25519-sha256"),
    ("config/sftp_vendor_feed.yaml", "CW-SHA1", "file_checksum: sha1"),
    ("batch_jobs/LedgerExportSigner.java", "QV-RSA", "interfaces.RSAPublicKey"),
    ("batch_jobs/LedgerExportSigner.java", "QV-DSA", 'getInstance("DSA")'),
    ("batch_jobs/LedgerExportSigner.java", "QV-KEYSIZE", "initialize(2048)"),
    ("batch_jobs/LedgerExportSigner.java", "CW-SHA1", '"SHA-1"'),
    ("batch_jobs/LedgerExportSigner.java", "QV-DH", '"DiffieHellman"'),
    ("batch_jobs/LedgerExportSigner.java", "QV-ECC", '"SHA256withECDSA"'),
    ("batch_jobs/LedgerExportSigner.java", "CW-CIPHER", '"DESede/CBC'),
    ("batch_jobs/archive_encrypt.py", "PQ-HYBRID", "hybrid X25519MLKEM768 exchange"),
    ("batch_jobs/archive_encrypt.py", "PQ-SYMMETRIC", "sealed with AES-256-GCM"),
    ("batch_jobs/archive_encrypt.py", "PQ-HYBRID", '"key_exchange": "X25519MLKEM768"'),
    ("batch_jobs/archive_encrypt.py", "PQ-KEM", '"ML-KEM-768"'),
    ("batch_jobs/archive_encrypt.py", "PQ-SIGNATURE", '"ML-DSA-65"'),   # NOT a DSA hit
    ("batch_jobs/archive_encrypt.py", "PQ-SYMMETRIC", '"cipher": "AES-256-GCM"'),
    ("batch_jobs/archive_encrypt.py", "PQ-HASH", '"SHA-384"'),
    ("batch_jobs/archive_encrypt.py", "PQ-KEM", '"ml_kem_768"'),
    ("batch_jobs/archive_encrypt.py", "PQ-SIGNATURE", '"ml_dsa_65"'),   # NOT a DSA hit
    ("infra/load_balancer.tf", "CW-TLS-LEGACY", "ELBSecurityPolicy-TLS-1-0-2015-04"),
    ("infra/load_balancer.tf", "CW-TLS-LEGACY", "ELBSecurityPolicy-2016-08"),  # allows TLS 1.0
)

# Files that must produce ZERO findings. They are the negative controls.
CLEAN_FILES = ("reporting/variance_report.py", "README.txt")


def resolve(root: Path) -> set[tuple[str, int, str]]:
    """Turn each anchor into (file, line number, rule) by searching the sample file.

    Raises if an anchor is missing or ambiguous, so a broken key fails loudly instead of
    shrinking silently.
    """
    expected = set()
    for rel, rule, anchor in PLANTED:
        lines = (root / rel).read_text(encoding="utf-8").splitlines()
        hits = [n for n, line in enumerate(lines, 1) if anchor in line]
        if len(hits) != 1:
            raise ValueError(f"anchor {anchor!r} matches {len(hits)} lines in {rel}")
        expected.add((rel, hits[0], rule))
    return expected
