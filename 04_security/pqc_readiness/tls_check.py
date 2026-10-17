"""Part 9 -- TLS post-quantum check: what an endpoint actually negotiates, seen from outside.

WHY THIS PART EXISTS
--------------------
Part 6 records what vendors SAY about hybrid key exchange (questionnaire Q5). This part
checks the one piece of that claim anyone can see from the outside. For each hostname in
a config file it makes ONE ordinary TLS handshake on port 443, the way a current browser
does: it offers the hybrid group X25519MLKEM768 (RFC 10024) alongside the classical
groups, and records what the server chose.

WHAT IT DOES, AND WHAT IT NEVER DOES
    * one handshake per listed host, port 443 only, with a pause between hosts
    * only the hosts in the config: no crawling, no subdomain discovery, no redirects
    * nothing is sent beyond the handshake and the closing alert: no HTTP request, no
      retries, no renegotiation, no session resumption
    * a failed handshake is recorded as failed, never as "classical"

HOW
    `openssl s_client` from an OpenSSL build that can offer X25519MLKEM768 (3.5 or later).
    Python's own ssl module cannot offer ML-KEM groups yet. The build is found by what it
    can do, not by its version string: PQC_OPENSSL first, then PATH, then common install
    locations. Its stdin is the null device, so after the handshake it reads end-of-input
    at once and closes.

    py tls_check.py                   demo: the public hosts in tls_hosts.yaml
                                      -> tls_demo_observations.json (committed evidence)
    py tls_check.py --vendors [FILE]  vendor hosts (default vendor_hosts.local.yaml, which
                                      is gitignored) -> vendor_observations.local.json

EVIDENCE (the migration register's style, one record per host)
    host, port, TLS version, cipher suite, key exchange group and its class, certificate
    key type and size, certificate signature algorithm, SHA-256 of the certificate, UTC
    timestamp, OpenSSL version, tool version, SHA-256 of the hosts file as parsed.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence

import yaml
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa

from common import HERE, TOOL_VERSION, force_utf8_console

PORT = 443
HOSTS_YAML = HERE / "tls_hosts.yaml"
DEMO_OBSERVATIONS = HERE / "tls_demo_observations.json"
VENDOR_HOSTS = HERE / "vendor_hosts.local.yaml"
VENDOR_HOSTS_EXAMPLE = HERE / "vendor_hosts.example.yaml"
VENDOR_OBSERVATIONS = HERE / "vendor_observations.local.json"
MAX_HOSTS = 25

# Offered like a current browser: key shares (*) for the hybrid and for X25519, with the
# NIST curves also accepted. Order is preference.
OFFERED_GROUPS = "*X25519MLKEM768:*X25519:secp256r1:secp384r1"
# s_client options that would send more than one handshake, or data after it.
FORBIDDEN_OPTIONS = frozenset({"-ign_eof", "-quiet", "-reconnect", "-starttls",
                               "-early_data", "-sess_in", "-sess_out", "-nbio_test",
                               "-prexit", "-keyupdate"})
# The null device as stdin: s_client sees end-of-input straight after the handshake and
# closes. (Windows note: an open but empty pipe makes OpenSSL wait instead.)
RUN_OPTIONS = {"stdin": subprocess.DEVNULL, "capture_output": True, "text": True,
               "encoding": "utf-8", "errors": "replace"}
COMMON_LOCATIONS = ("C:/Program Files/OpenSSL-Win64/bin/openssl.exe",
                    "C:/Program Files/OpenSSL/bin/openssl.exe",
                    "/opt/homebrew/opt/openssl@3/bin/openssl",
                    "/usr/local/opt/openssl@3/bin/openssl",
                    "/usr/local/bin/openssl", "/usr/bin/openssl")

# ---- key exchange classes (group names as OpenSSL prints them, compared casefolded) ----
HYBRID, PQC_ONLY, CLASSICAL, UNKNOWN = "hybrid PQC", "PQC only", "classical", "unknown"
HYBRID_GROUPS = frozenset({"x25519mlkem768", "secp256r1mlkem768", "secp384r1mlkem1024",
                           "x448mlkem1024"})
PQC_GROUPS = frozenset({"mlkem512", "mlkem768", "mlkem1024"})
CLASSICAL_GROUPS = frozenset({"x25519", "x448", "secp256r1", "prime256v1", "secp384r1",
                              "secp521r1", "ffdhe2048", "ffdhe3072", "ffdhe4096",
                              "ffdhe6144", "ffdhe8192"})
QUANTUM_SAFE = (HYBRID, PQC_ONLY)
PRE_TLS13 = frozenset({"TLSv1.2", "TLSv1.1", "TLSv1", "SSLv3"})


def group_class(group: str) -> str:
    """Anything not recognized, including a group the transcript did not show, is UNKNOWN.
    It is never assumed to be classical."""
    name = group.strip().casefold()
    if name in HYBRID_GROUPS:
        return HYBRID
    if name in PQC_GROUPS:
        return PQC_ONLY
    if name in CLASSICAL_GROUPS or name.startswith("dh "):
        return CLASSICAL
    return UNKNOWN


def exchange_class(tls_version: str, group: str) -> str:
    """Hybrid and ML-KEM groups exist only in TLS 1.3, so any earlier version is classical
    whatever the transcript shows (static-RSA key exchange prints no group at all)."""
    return CLASSICAL if tls_version in PRE_TLS13 else group_class(group)


# ---- hosts config ----------------------------------------------------------------------
class HostsError(ValueError):
    """The hosts file is malformed or lists something that is not a plain hostname."""


@dataclass(frozen=True)
class Target:
    host: str
    vendor: str = ""


@dataclass(frozen=True)
class HostsConfig:
    targets: tuple[Target, ...]
    delay_seconds: int
    timeout_seconds: int
    sha256: str
    label: str


# A plain DNS name: letters, digits and hyphens, at least one dot, an alphabetic (or
# punycode) top-level label. No scheme, port, path, wildcard or IP address can match.
HOSTNAME = re.compile(r"(?=.{4,253}$)(?:(?!-)[a-z0-9-]{1,63}(?<!-)\.)+"
                      r"(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})")


def _int(data: dict, key: str, low: int, high: int, where: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise HostsError(f"{where}: {key} must be a whole number from {low} to {high}, "
                         f"got {value!r}")
    return value


def _host(value, where: str) -> str:
    if not isinstance(value, str) or not HOSTNAME.fullmatch(value.strip().lower()):
        raise HostsError(f"{where}: {value!r} is not a plain hostname (no scheme, port, "
                         "path, wildcard or IP address)")
    return value.strip().lower()


def _vendor_targets(entries, where: str) -> list[Target]:
    if not isinstance(entries, list) or not entries:
        raise HostsError(f"{where}: vendors must be a non-empty list")
    targets, names = [], set()
    for n, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != {"vendor", "hosts"}:
            raise HostsError(f"{where}: vendor entry {n} must have exactly vendor and hosts")
        name = entry["vendor"].strip() if isinstance(entry["vendor"], str) else ""
        if not name or name.casefold() in names:
            raise HostsError(f"{where}: vendor entry {n} needs a unique vendor name")
        names.add(name.casefold())
        hosts = entry["hosts"]
        if not isinstance(hosts, list) or not hosts:
            raise HostsError(f"{where}: {name}: hosts must be a non-empty list")
        targets += [Target(_host(h, f"{where}: {name}"), name) for h in hosts]
    return targets


def parse_hosts(raw: bytes, label: str, vendor_mode: bool = False) -> HostsConfig:
    try:
        data = yaml.safe_load(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HostsError(f"{label}: not readable YAML ({exc})") from exc
    key = "vendors" if vendor_mode else "hosts"
    if not isinstance(data, dict):
        raise HostsError(f"{label}: expected a mapping with delay_seconds, "
                         f"timeout_seconds and {key}")
    unknown = sorted(set(data) - {"delay_seconds", "timeout_seconds", key})
    if unknown:
        raise HostsError(f"{label}: unknown key(s) {unknown}")
    delay = _int(data, "delay_seconds", 1, 60, label)
    timeout = _int(data, "timeout_seconds", 5, 60, label)
    if vendor_mode:
        targets = _vendor_targets(data.get(key), label)
    else:
        hosts = data.get(key)
        if not isinstance(hosts, list) or not hosts:
            raise HostsError(f"{label}: hosts must be a non-empty list")
        targets = [Target(_host(h, label)) for h in hosts]
    seen = [t.host for t in targets]
    if len(set(seen)) != len(seen):
        raise HostsError(f"{label}: each host may be listed once (one handshake per host)")
    if len(targets) > MAX_HOSTS:
        raise HostsError(f"{label}: at most {MAX_HOSTS} hosts per run, got {len(targets)}")
    return HostsConfig(tuple(targets), delay, timeout, hashlib.sha256(raw).hexdigest(), label)


def load_hosts(path: Path, vendor_mode: bool = False) -> HostsConfig:
    return parse_hosts(path.read_bytes(), path.name, vendor_mode)


# ---- one handshake ----------------------------------------------------------------------
Runner = Callable[[Sequence[str], float], tuple[int | None, str]]


def command(openssl: str, host: str, port: int = PORT, connect: str = "") -> list[str]:
    """`connect` overrides only the address dialed (the local self-test uses 127.0.0.1)."""
    return [openssl, "s_client", "-connect", f"{connect or host}:{port}", "-servername",
            host, "-groups", OFFERED_GROUPS, "-alpn", "h2,http/1.1", "-no_ign_eof"]


def run_openssl(argv: Sequence[str], timeout: float) -> tuple[int | None, str]:
    try:
        done = subprocess.run(list(argv), timeout=timeout, **RUN_OPTIONS)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


@dataclass(frozen=True)
class Observation:
    observation_id: str
    vendor: str
    host: str
    port: int
    status: str                      # ok | failed
    tls_version: str
    cipher_suite: str
    key_exchange_group: str
    key_exchange_class: str
    cert_key_type: str
    cert_key_bits: int | None
    cert_signature_algorithm: str
    cert_sha256: str
    error: str
    observed_at: str
    offered_groups: str
    openssl_version: str
    tool_version: str
    hosts_config_sha256: str


FIELDS = tuple(Observation.__dataclass_fields__)
# Evidence every completed handshake must carry (the group may be blank only when its
# class is UNKNOWN, which run checks enforce separately).
EVIDENCE_FIELDS = ("tls_version", "cipher_suite", "key_exchange_class", "cert_key_type",
                   "cert_signature_algorithm", "cert_sha256", "observed_at",
                   "offered_groups", "openssl_version", "tool_version",
                   "hosts_config_sha256")

CIPHER_LINE = re.compile(r"^New, (\S+), Cipher is (\S+)", re.M)
NEGOTIATED = re.compile(r"^Negotiated TLS1\.3 group:\s*(\S+)", re.M)
TEMP_KEY = re.compile(r"^(?:Peer|Server) Temp Key:\s*(.+?)\s*$", re.M)
PEM = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)
ALERT = re.compile(r"alert ([a-z ]+?)(?::|$)", re.M)


def observation_id(host: str, port: int) -> str:
    return "T-" + hashlib.sha256(f"{host}:{port}".encode()).hexdigest()[:8]


def _temp_key_group(value: str) -> str:
    """'X25519, 253 bits' -> X25519; 'ECDH, secp384r1, 384 bits' -> secp384r1;
    'DH, 2048 bits' -> 'DH 2048 bits'."""
    parts = [p.strip() for p in value.split(",")]
    if parts[0] == "ECDH" and len(parts) > 1:
        return parts[1]
    if parts[0] == "DH":
        return "DH " + (parts[1] if len(parts) > 1 else "")
    return parts[0]


def negotiated_group(text: str) -> str:
    m = NEGOTIATED.search(text)
    if m:
        return m.group(1)
    t = TEMP_KEY.search(text)
    return _temp_key_group(t.group(1)) if t else ""


UNPARSEABLE = "certificate not parseable"


def cert_facts(pem: str) -> tuple[str, int | None, str, str]:
    """(key type, key bits, signature algorithm, SHA-256 of the DER) of one certificate.
    A certificate the library cannot parse is recorded as such, with the hash of its
    bytes, rather than stopping the run and losing the evidence already gathered."""
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
    except ValueError:
        body = "".join(pem.splitlines()[1:-1])
        try:
            der = base64.b64decode(body, validate=True)
        except binascii.Error:
            der = pem.encode("ascii", "replace")
        return UNPARSEABLE, None, UNPARSEABLE, hashlib.sha256(der).hexdigest()
    try:
        key = cert.public_key()
    except (ValueError, UnsupportedAlgorithm):
        key = None
    if isinstance(key, rsa.RSAPublicKey):
        kind, bits = "RSA", key.key_size
    elif isinstance(key, ec.EllipticCurvePublicKey):
        kind, bits = f"EC {key.curve.name}", key.key_size
    elif isinstance(key, ed25519.Ed25519PublicKey):
        kind, bits = "Ed25519", 256
    elif isinstance(key, ed448.Ed448PublicKey):
        kind, bits = "Ed448", 456
    elif isinstance(key, dsa.DSAPublicKey):
        kind, bits = "DSA", key.key_size
    else:
        kind, bits = f"other ({cert.public_key_algorithm_oid.dotted_string})", None
    oid = cert.signature_algorithm_oid
    sig = oid._name if oid._name != "Unknown OID" else oid.dotted_string
    der = cert.public_bytes(serialization.Encoding.DER)
    return kind, bits, sig, hashlib.sha256(der).hexdigest()


def failure_reason(text: str, returncode: int | None) -> str:
    """A short, fixed-vocabulary reason; raw OpenSSL error text is never stored."""
    low = text.lower()
    if returncode is None:
        return "timed out"
    if "bio_lookup" in low or "getaddrinfo" in low or "nodename nor servname" in low:
        return "name lookup failed"
    alert = ALERT.search(low)
    if alert:
        return f"handshake refused (alert: {alert.group(1).strip()})"
    if "connect:errno" in low or "connect error" in low:
        return "connection failed"
    return "handshake did not complete"


def parse_transcript(text: str, returncode: int | None, target: Target, observed_at: str,
                     openssl_version: str, hosts_sha256: str,
                     port: int = PORT) -> Observation:
    cipher = CIPHER_LINE.search(text)
    pem = PEM.search(text)
    done = bool(cipher) and "NONE" not in cipher.group(1) and "NONE" not in cipher.group(2)
    common = dict(observation_id=observation_id(target.host, port), vendor=target.vendor,
                  host=target.host, port=port, observed_at=observed_at,
                  offered_groups=OFFERED_GROUPS, openssl_version=openssl_version,
                  tool_version=TOOL_VERSION, hosts_config_sha256=hosts_sha256)
    if not (done and pem):
        return Observation(status="failed", tls_version="", cipher_suite="",
                           key_exchange_group="", key_exchange_class="", cert_key_type="",
                           cert_key_bits=None, cert_signature_algorithm="", cert_sha256="",
                           error=failure_reason(text, returncode), **common)
    group = negotiated_group(text)
    kind, bits, sig, digest = cert_facts(pem.group(0))
    return Observation(status="ok", tls_version=cipher.group(1), cipher_suite=cipher.group(2),
                       key_exchange_group=group,
                       key_exchange_class=exchange_class(cipher.group(1), group),
                       cert_key_type=kind, cert_key_bits=bits, cert_signature_algorithm=sig,
                       cert_sha256=digest, error="", **common)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iter_hosts(cfg: HostsConfig, openssl: str, openssl_version: str,
               runner: Runner = run_openssl, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], str] = utc_now) -> Iterator[Observation]:
    """Exactly one runner call per target, in config order, pausing between targets.
    A failure is recorded and the run moves on: nothing is ever retried."""
    for n, target in enumerate(cfg.targets):
        if n:
            sleep(cfg.delay_seconds)
        observed_at = clock()
        returncode, text = runner(command(openssl, target.host), cfg.timeout_seconds)
        yield parse_transcript(text, returncode, target, observed_at, openssl_version,
                               cfg.sha256)


def check_hosts(cfg: HostsConfig, openssl: str, openssl_version: str,
                runner: Runner = run_openssl, sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], str] = utc_now) -> list[Observation]:
    return list(iter_hosts(cfg, openssl, openssl_version, runner, sleep, clock))


# ---- finding a capable OpenSSL (local commands only, nothing on the network) ----------
def candidates() -> list[str]:
    found = [os.environ.get("PQC_OPENSSL", "").strip(), shutil.which("openssl") or ""]
    return list(dict.fromkeys(p for p in found + list(COMMON_LOCATIONS) if p))


def offers_hybrid(exe: str, runner: Runner = run_openssl) -> bool:
    returncode, out = runner([exe, "list", "-tls-groups", "-tls1_3"], 10)
    return returncode == 0 and "X25519MLKEM768" in re.split(r"[:\s]+", out)


def find_openssl(runner: Runner = run_openssl) -> tuple[str, str] | None:
    """(executable, version line) of the first build that can offer X25519MLKEM768."""
    for exe in candidates():
        if Path(exe).is_file() and offers_hybrid(exe, runner):
            return exe, runner([exe, "version"], 10)[1].strip()
    return None


# ---- evidence file -----------------------------------------------------------------------
class ObservationsError(ValueError):
    """An observations file is unreadable or its records lack the evidence fields."""


def write_observations(path: Path, cfg: HostsConfig, observations: list[Observation]) -> None:
    doc = {"automation": "pqc-readiness-toolkit tls_check", "tool_version": TOOL_VERSION,
           "hosts_config": cfg.label, "hosts_config_sha256": cfg.sha256, "port": PORT,
           "delay_seconds": cfg.delay_seconds, "offered_groups": OFFERED_GROUPS,
           "observations": [asdict(o) for o in observations]}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def load_observations(path: Path) -> tuple[dict, list[Observation]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc["observations"]
        return doc, [Observation(**{k: row[k] for k in FIELDS}) for row in rows]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ObservationsError(f"{path.name}: not a tls_check observations file ({exc})") \
            from exc


# ---- command line ------------------------------------------------------------------------
def git_ignored(path: Path) -> bool | None:
    """True / False as git reports it; None when git cannot say (no git, or outside a repo,
    where nothing can be committed by accident)."""
    try:
        done = subprocess.run(["git", "-C", str(HERE), "check-ignore", "-q", str(path)],
                              capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {0: True, 1: False}.get(done.returncode)


def print_observations(observations: list[Observation]) -> None:
    for o in observations:
        if o.status == "ok":
            print(f"    {o.host:<28} {o.tls_version:<8} {o.key_exchange_group or '?':<16} "
                  f"{o.key_exchange_class:<11} {o.cert_key_type} {o.cert_key_bits or ''} "
                  f"{o.cert_signature_algorithm}")
        else:
            print(f"    {o.host:<28} FAILED   {o.error}")


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_console()
    parser = argparse.ArgumentParser(description="One TLS handshake per listed host on port "
                                     "443, recording the key exchange group chosen.")
    parser.add_argument("--vendors", nargs="?", const=str(VENDOR_HOSTS), metavar="HOSTS_YAML",
                        help="vendor hosts file (default: vendor_hosts.local.yaml)")
    args = parser.parse_args(argv)
    vendor_mode = args.vendors is not None
    hosts_path = Path(args.vendors).resolve() if vendor_mode else HOSTS_YAML
    out = VENDOR_OBSERVATIONS if vendor_mode else DEMO_OBSERVATIONS
    if vendor_mode:
        exposed = [p.name for p in (hosts_path, out) if git_ignored(p) is False]
        if exposed:
            print(f"REFUSED: {exposed} would not be ignored by git. Real vendor hostnames "
                  "and results stay in gitignored *.local.* files.")
            return 2
    try:
        cfg = load_hosts(hosts_path, vendor_mode)
    except (HostsError, OSError) as exc:
        print(f"HOSTS ERROR: {exc}")
        return 2
    found = find_openssl()
    if found is None:
        print("No OpenSSL build that can offer X25519MLKEM768 was found (needs 3.5 or "
              "later). Point PQC_OPENSSL at one.")
        return 2
    exe, version = found
    print(f"{version}: one handshake each to {len(cfg.targets)} host(s) from {cfg.label}, "
          f"port {PORT}, {cfg.delay_seconds} s apart")
    observations: list[Observation] = []
    try:                    # whatever happens, the hosts already contacted keep their record
        for o in iter_hosts(cfg, exe, version):
            observations.append(o)
            print_observations([o])
    finally:
        write_observations(out, cfg, observations)
        print(f"  wrote {out.name} ({len(observations)} of {len(cfg.targets)} hosts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
