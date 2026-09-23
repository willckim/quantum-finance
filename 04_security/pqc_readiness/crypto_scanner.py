"""Part 3 -- Crypto inventory scanner: find the cryptography a codebase depends on.

WHY AN INVENTORY COMES FIRST
----------------------------
An organization cannot migrate cryptography it does not know it has. Every PQC roadmap
(NIST, CISA, the UK NCSC, PCI DSS 4.0 Requirement 12.3.3) starts the same way: list
where each algorithm is used, then rank what to replace. This scanner produces a first
draft of that list by walking a folder and matching known algorithm names line by line.

THE THREE CLASSES
-----------------
  QUANTUM-VULNERABLE  Sound today, broken by Shor's algorithm once a large enough quantum
                      computer exists: RSA, elliptic curves (ECDSA, ECDH, X25519,
                      Ed25519), finite-field Diffie-Hellman and DSA. Key size does not
                      rescue them: Shor's cost grows only polynomially, so RSA-4096 buys
                      a few years at most, not safety. That is why a hardcoded RSA key
                      size of 2048 or more is reported here, and not as "fine".
  CLASSICALLY WEAK    Already broken or deprecated WITHOUT a quantum computer: MD5, SHA-1,
                      DES and 3DES, RC4, TLS 1.0 and 1.1, SSL, RSA/DH keys under 2048
                      bits. These are the most urgent, because the attack exists today.
  PQC-READY           Standardized post-quantum algorithms (ML-KEM, ML-DSA, SLH-DSA),
                      hybrid key exchange, and symmetric primitives large enough to shrug
                      off Grover's algorithm (AES-256, SHA-384/512, SHA-3). Reported so
                      the inventory shows progress, not only problems.

Deliberately NOT flagged: AES-128 and SHA-256. Grover's algorithm halves their effective
strength, but NIST's transition guidance keeps them acceptable, so flagging them would
bury real findings in noise.

READ-ONLY BY CONSTRUCTION
-------------------------
The scanner opens files in binary read mode only ("rb"), never follows symbolic links,
and never writes inside the folder it scans. run_all.py proves this on every run by
hashing every file before and after the scan, and proves that the hash comparison can
fail by applying it to a scratch copy that it deliberately changes.

LIMITATIONS (it is a first-draft inventory, not a verdict)
----------------------------------------------------------
* Pattern matching, not parsing. A comment that says "we removed RSA" is still flagged.
  That is the right bias for an inventory (a human triages false positives cheaply;
  a missed dependency is expensive), but each finding needs review.
* It sees names, not behavior. Crypto chosen at runtime from a database value, or hidden
  inside a compiled library, a vendor appliance or a cloud service default, is invisible.
  A complete inventory also needs network scans of live TLS endpoints and vendor
  attestations.
* Its rules were graded against a planted sample (see sample_manifest.py for what that
  proves and what it cannot).

Run on any folder (read-only):  py crypto_scanner.py <folder>
or set PQC_SCAN_ROOT before run_all.py. The demo only ever scans sample_codebase/.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common import SAMPLE_CODEBASE, Check, not_run, section
from sample_manifest import CLEAN_FILES, resolve

QV, CW, PQ = "quantum-vulnerable", "classically weak", "PQC-ready"

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}
MAX_FILE_BYTES = 1_000_000          # larger files are skipped and logged as exceptions
BINARY_SNIFF_BYTES = 8_192

# Letter-only boundaries: "id_rsa" and "RSA2048" match, "parsable" does not. (Python's \b
# treats "_" as a word character, which would miss rsa_key and id_rsa.)
_L, _R = r"(?<![A-Za-z])", r"(?![A-Za-z])"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    what: str
    pattern: re.Pattern[str]
    # Some rules decide their class from the match (key sizes). Returns None to ignore.
    classify: Callable[[re.Match[str]], str | None] | None = None


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _key_size_class(match: re.Match[str]) -> str | None:
    bits = int(match.group("bits"))
    # 128/256/384 are symmetric or curve sizes and 521 is the P-521 curve: not RSA/DH moduli.
    if bits < 512 or bits == 521:
        return None
    return CW if bits < 2048 else QV


def _cs(pattern: str) -> str:
    """Case-SENSITIVE island inside an otherwise case-insensitive rule.

    Short names such as DES and DH are only trustworthy in capitals: "Des chiffres" and a
    variable named dh are not cryptography.
    """
    return f"(?-i:{pattern})"


RULES: tuple[Rule, ...] = (
    # --- classically weak: broken without a quantum computer ---------------------------
    Rule("CW-MD5", CW, "MD5 hash (collisions practical since 2004)", _rx(_L + r"MD5" + _R)),
    Rule("CW-SHA1", CW, "SHA-1 hash (collision shown 2017; NIST retiring it)",
         _rx(_L + r"SHA-?1(?![0-9])|" + _cs(r"-SHA") + r"(?=[\s:;,\"']|$)")),
    # Long names first: the first alternative that matches wins, and "DES-EDE3-CBC" must
    # be reported as 3DES, not as the bare "DES" at its start.
    Rule("CW-CIPHER", CW, "DES, 3DES or RC4 cipher",
         _rx(_L + r"(3DES|DESede|TripleDES|DES-EDE3?|DES[-_](CBC|ECB)|RC4|ARCFOUR)" + _R
             + "|" + _cs(_L + r"DES" + _R))),
    Rule("CW-TLS-LEGACY", CW, "SSL or TLS 1.0 / 1.1 protocol version",
         _rx(_L + r"SSL[\s_-]?v?[23](?:[._]0)?(?![0-9.])"
             + r"|TLS[\s_-]?v?1[._-][01](?![0-9])|TLSv1(?![._-]?[0-9])"
             + r"|ELBSecurityPolicy-2016-08")),        # AWS preset that still allows TLS 1.0
    Rule("CW-KEYSIZE", CW, "hardcoded RSA/DH/DSA key size under 2048 bits",
         _rx(r"(?<![A-Za-z0-9_])(key_?size|bits|modulus|initialize\()\W{0,3}(?P<bits>\d{3,5})"),
         _key_size_class),
    # --- quantum-vulnerable: sound today, broken by Shor ---------------------------------
    Rule("QV-RSA", QV, "RSA encryption or signature",
         _rx(_L + r"RSA" + _R + r"|" + _L + r"RSA(?=SSA|Public|Private|Key|Encrypt|Sign)"
             + r"|withRSA|" + _L + r"[RP]S(256|384|512)(?![0-9])")),
    Rule("QV-ECC", QV, "elliptic-curve crypto (ECDSA, ECDH, X25519, Ed25519, NIST curves)",
         _rx(_L + r"(ECDSA|ECDHE?|EdDSA|Ed25519|Ed448|X448|Curve25519|ES(256|384|512))" + _R
             + r"|" + _L + r"ECDSA(?=Public|Private|Key|Sign)|withECDSA|EllipticCurve"
             + r"|" + _L + r"X25519(?!MLKEM|Kyber)|secp\d{3}[rk]1(?!MLKEM)|prime256v1"
             + r"|nistp\d{3}|" + _L + r"P-(256|384|521)(?![0-9])")),
    Rule("QV-DH", QV, "finite-field Diffie-Hellman key exchange",
         _rx(_cs(_L + r"DHE?" + _R) + r"|" + _L + r"(DiffieHellman|Diffie-Hellman|ffdhe\d+)"
             + _R + r"|dhparam|" + _L + r"dh\.(generate_parameters|DHParameter)")),
    Rule("QV-DSA", QV, "DSA signature",
         # ML-DSA and SLH-DSA are post-quantum: never let their "DSA" count as classical DSA.
         _rx(r"(?<!ML[-_])(?<!SLH[-_])" + _L + r"DSA(" + _R + r"|(?=Public|Private|Key|Sign))"
             + r"|ssh-dss|withDSA")),
    # QV-KEYSIZE shares CW-KEYSIZE's pattern; the classifier routes each hit to one of them.
    # --- PQC-ready ---------------------------------------------------------------------
    Rule("PQ-HYBRID", PQ, "hybrid classical + post-quantum key exchange",
         _rx(r"X25519MLKEM768|SecP256r1MLKEM768|SecP384r1MLKEM1024|X25519Kyber768")),
    Rule("PQ-KEM", PQ, "ML-KEM key encapsulation (FIPS 203)",
         _rx(r"(?<![A-Za-z0-9])ML[-_]?KEM")),
    Rule("PQ-SIGNATURE", PQ, "ML-DSA or SLH-DSA signature (FIPS 204 / 205)",
         _rx(r"ML[-_]?DSA|SLH[-_]?DSA")),
    Rule("PQ-SYMMETRIC", PQ, "AES-256 (Grover-resistant key length)",
         _rx(_L + r"AES[-_]?256")),
    Rule("PQ-HASH", PQ, "SHA-384, SHA-512 or SHA-3 hash",
         _rx(_L + r"(SHA-?(384|512)(?![0-9])|SHA-?3(?![0-9]))")),
)


@dataclass(frozen=True)
class Finding:
    path: str               # relative to the scan root, forward slashes (no machine paths)
    line: int
    rule_id: str
    category: str
    what: str
    match: str
    snippet: str


@dataclass
class ScanResult:
    root_label: str
    files_scanned: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # SHA-256 of the exact bytes each scanned file held when it was read (evidence).
    file_sha256: dict[str, str] = field(default_factory=dict)

    def by_category(self) -> Counter[str]:
        return Counter(f.category for f in self.findings)

    def counts(self) -> Counter[tuple[str, str]]:
        return Counter((f.path, f.rule_id) for f in self.findings)


def _is_link(path: str) -> bool:
    """True for symlinks AND Windows junctions.

    GOTCHA: on Python 3.10, os.walk(followlinks=False) and Path.is_symlink() do not treat
    an NTFS junction as a link, so a junction inside the scan root silently leads outside
    it. Comparing the resolved path with the literal one catches both kinds.
    """
    parent = os.path.realpath(os.path.dirname(path))
    return os.path.islink(path) or os.path.realpath(path) != os.path.join(
        parent, os.path.basename(path))


def iter_files(root: Path, skipped: list[tuple[str, str]] | None = None):
    """Every regular file under root, sorted. Links are never followed, only logged."""
    def note(path: str) -> None:
        if skipped is not None:
            skipped.append((Path(path).relative_to(root).as_posix(), "link, not followed"))

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        keep = []
        for name in sorted(d for d in dirnames if d not in SKIP_DIRS):
            full = os.path.join(dirpath, name)
            if _is_link(full):
                note(full)
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _is_link(full):
                note(full)
            else:
                yield Path(full)


def scan_line(text: str) -> list[tuple[Rule, str, str, str]]:
    """(rule, rule id, category, matched text) for every rule that fires on one line.

    Rules with a classifier (key sizes) look at EVERY match on the line and keep the worst
    one, so "bits=128, key_size=1024" reports the 1024.

    WEAKEST LINK: a line that names quantum-vulnerable or weak crypto is not PQC-ready
    just because it also names a strong piece. "DHE-RSA-AES256-SHA256" uses AES-256, but
    its key exchange falls to Shor, so the AES-256 hit is dropped rather than counted as
    migration progress.
    """
    hits = []
    for rule in RULES:
        if rule.classify:
            classed = [(rule.classify(m), m) for m in rule.pattern.finditer(text)]
            classed = [(c, m) for c, m in classed if c is not None]
            if not classed:
                continue
            category, match = min(classed, key=lambda cm: cm[0] != CW)   # CW beats QV
        else:
            match = rule.pattern.search(text)
            if not match:
                continue
            category = rule.category
        rule_id = rule.rule_id.replace("CW-", "QV-") if category == QV else rule.rule_id
        hits.append((rule, rule_id, category, match.group(0)))
    if any(category != PQ for _, _, category, _ in hits):
        hits = [hit for hit in hits if hit[2] != PQ]
    return hits


def scan(root: Path, root_label: str | None = None) -> ScanResult:
    if not root.is_dir():
        raise NotADirectoryError(f"scan root is not a folder: {root_label or root.name}")
    result = ScanResult(root_label or root.name)
    for path in iter_files(root, result.skipped):
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            result.skipped.append((rel, f"larger than {MAX_FILE_BYTES:,} bytes"))
            continue
        with path.open("rb") as handle:          # read-only, binary: never "w", "a" or "+"
            data = handle.read()
        if b"\0" in data[:BINARY_SNIFF_BYTES]:
            result.skipped.append((rel, "binary file"))
            continue
        result.files_scanned += 1
        result.file_sha256[rel] = hashlib.sha256(data).hexdigest()   # the bytes scanned
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            for rule, rule_id, category, matched in scan_line(line):
                what = ("hardcoded key size (Shor ignores key length)"
                        if rule_id == "QV-KEYSIZE" else rule.what)
                result.findings.append(Finding(rel, number, rule_id, category, what,
                                               matched, line.strip()[:100]))
    return result


# ---- read-only proof ---------------------------------------------------------------
def snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    """{relative path: (sha256, size, mtime_ns)} for every file under root."""
    snap = {}
    for path in iter_files(root):
        stat = path.stat()
        with path.open("rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        snap[path.relative_to(root).as_posix()] = (digest, stat.st_size, stat.st_mtime_ns)
    return snap


def snapshot_diff(before: dict, after: dict) -> list[str]:
    """Paths added, removed or changed between two snapshots. Empty means untouched."""
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def print_findings(result: ScanResult) -> None:
    print(f"  Scanned {result.files_scanned} files under {result.root_label}/, "
          f"{len(result.findings)} findings, {len(result.skipped)} skipped.")
    for category in (CW, QV, PQ):
        rows = [f for f in result.findings if f.category == category]
        print(f"\n  {category.upper()} ({len(rows)})")
        for f in rows:
            print(f"    {f.path}:{f.line:<4} {f.rule_id:<14} {f.snippet[:60]}")
    for rel, reason in result.skipped:
        print(f"  skipped {rel}: {reason}")


def _read_only_checks(root: Path, before: dict, after: dict) -> list[Check]:
    changed = snapshot_diff(before, after)
    checks = [Check("Scanner: read-only (every file's SHA-256, size, mtime unchanged)",
                    not changed and len(before) > 0,
                    f"{len(before)} files hashed before and after"
                    + (f"; CHANGED: {changed}" if changed else ""))]
    # The read-only check must be able to fail. Apply the same comparison to a scratch
    # copy, change one byte, and require it to notice.
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "copy"
        shutil.copytree(SAMPLE_CODEBASE, copy)
        victim = next(iter_files(copy))
        base = snapshot(copy)
        victim.write_bytes(victim.read_bytes() + b"#")      # scratch copy only
        caught = snapshot_diff(base, snapshot(copy))
    checks.append(Check("Scanner: read-only check detects a deliberate change (control)",
                        caught == [victim.relative_to(copy).as_posix()], f"flagged {caught}"))
    return checks


def _make_dir_link(link: Path, target: Path) -> bool:
    """A directory symlink, or on Windows a junction (needs no admin rights)."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return True
        except OSError:
            pass
    return False


def _link_escape_check() -> Check:
    """A link inside the scan root that points outside it must be skipped, not followed."""
    name = "Scanner: never follows a directory link out of the scan root"
    with tempfile.TemporaryDirectory() as scratch:
        root, outside = Path(scratch) / "root", Path(scratch) / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "inside.cfg").write_text('alg = "RSA"\n', encoding="utf-8")
        (outside / "leak.cfg").write_text('alg = "RSA"\n', encoding="utf-8")
        if not _make_dir_link(root / "link", outside):
            return not_run(name, "this OS user cannot create a directory link")
        result = scan(root)
        seen = sorted({f.path for f in result.findings} | set(snapshot(root)))
        logged = any(path == "link" for path, _ in result.skipped)
    return Check(name, seen == ["inside.cfg"] and logged,
                 f"saw {seen}; link logged as skipped: {logged}")


def _planted_checks(root: Path, result: ScanResult) -> list[Check]:
    expected = resolve(root)
    got = {(f.path, f.line, f.rule_id) for f in result.findings}
    missed, extra = sorted(expected - got), sorted(got - expected)
    checks = [
        Check("Scanner: finds every planted finding on its exact line",
              not missed, f"{len(expected)} planted" + (f"; MISSED {missed}" if missed else "")),
        Check("Scanner: no findings beyond the planted ones (precision)",
              not extra, f"UNEXPECTED {extra}" if extra else "0 unexpected"),
    ]
    noisy = [f for f in CLEAN_FILES if any(g[0] == f for g in got)]
    checks.append(Check("Scanner: negative-control files full of near-miss words stay clean",
                        not noisy and all((root / f).is_file() for f in CLEAN_FILES),
                        ", ".join(CLEAN_FILES) + (f"; NOISY {noisy}" if noisy else "")))
    fired = {f.rule_id for f in result.findings}
    all_ids = {r.rule_id for r in RULES} | {"QV-KEYSIZE"}
    checks.append(Check("Scanner: every rule fires at least once on the sample",
                        fired == all_ids, f"{len(fired)}/{len(all_ids)} rules"))
    return checks


def part3_scan(root: Path) -> tuple[ScanResult, list[Check]]:
    section("PART 3 -- CRYPTO INVENTORY SCANNER (read-only)")
    is_sample = root == SAMPLE_CODEBASE
    label = root.name if is_sample else "custom-scan-root"   # never print machine paths
    before = snapshot(root)
    result = scan(root, label)
    after = snapshot(root)
    print_findings(result)
    totals = result.by_category()
    print(f"\n  Totals: {totals[CW]} classically weak, {totals[QV]} quantum-vulnerable, "
          f"{totals[PQ]} PQC-ready.")
    checks = _read_only_checks(root, before, after) + [_link_escape_check()]
    if is_sample:
        checks += _planted_checks(root, result)
    else:
        checks.append(not_run("Scanner: planted-finding checks",
                              "they only apply to sample_codebase/"))
    return result, checks


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).with_name(
        "sample_codebase")
    print_findings(scan(target))
