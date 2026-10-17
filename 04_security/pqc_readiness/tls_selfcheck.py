"""Part 9 in the run: the TLS check's self-checks, the demo evidence and the vendor join.

run_all.py never contacts a public host. It reads the evidence tls_check.py wrote
(tls_demo_observations.json, and vendor observations only when PQC_VENDOR_OBSERVATIONS
names a file), checks it, and grades the checker itself offline:

  * hosts files: every malformed entry rejected, every valid form accepted
  * the command: exactly one plain handshake, nothing that sends more (with a control)
  * the loop: one call per host, a pause between hosts, no retry (with a retrying mutant)
  * the parser: hand-written transcripts in each format OpenSSL prints, failures included
  * the classifier: group names from RFC 10024 and the IANA registry, by hand
  * a live handshake to a LOCAL test server (127.0.0.1) offering only the hybrid, and one
    offering only X25519, with certificates generated here: the expected answer is set
    by the server's configuration, not read from the checker
  * the real-vendor files are gitignored and untracked
"""

from __future__ import annotations

import hashlib
import socket
import subprocess
import tempfile
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID

from common import HERE, TOOL_VERSION, Check, not_run, section
from tls_check import (CLASSICAL, DEMO_OBSERVATIONS, EVIDENCE_FIELDS, FORBIDDEN_OPTIONS,
                       HOSTS_YAML, HYBRID, MAX_HOSTS, OFFERED_GROUPS, PQC_ONLY, PORT,
                       RUN_OPTIONS, UNKNOWN, UNPARSEABLE, VENDOR_HOSTS,
                       VENDOR_HOSTS_EXAMPLE, HostsError, Observation, ObservationsError,
                       Target, cert_facts, check_hosts, command, exchange_class, find_openssl,
                       git_ignored, group_class, load_hosts, load_observations,
                       parse_hosts, parse_transcript, print_observations, run_openssl)
from vendor_observed import (CONTRADICTED, NOT_OBSERVED, VendorClaim, claim_checks,
                             compare, load_vendor_observations, write_observed_tracker)
from vendor_review import SAMPLE_TRACKER, Vendor

OBSERVED_TRACKER = HERE / "vendor_tracker_observed.csv"
OBSERVED_TRACKER_LOCAL = HERE / "vendor_tracker_observed.local.csv"
STAMP = "2026-01-01T00:00:00+00:00"


# ---- hosts files ---------------------------------------------------------------------------
def _yaml(hosts: Sequence[str], extra: str = "", delay="3", timeout="15") -> bytes:
    lines = [f"delay_seconds: {delay}", f"timeout_seconds: {timeout}", "hosts:"]
    return ("\n".join(lines + [f"  - '{h}'" for h in hosts]) + "\n" + extra).encode()


VENDOR_YAML = ("delay_seconds: 3\ntimeout_seconds: 15\nvendors:\n"
               "  - vendor: Vendor A\n    hosts: [a.example.org, api.a.example.org]\n")
BROKEN_HOSTS = {
    "scheme": _yaml(["https://www.example.org"]), "port": _yaml(["www.example.org:443"]),
    "path": _yaml(["www.example.org/login"]), "wildcard": _yaml(["*.example.org"]),
    "IPv4 address": _yaml(["192.0.2.10"]), "IPv6 address": _yaml(["2001:db8::1"]),
    "single-label name": _yaml(["localhost"]), "trailing dot": _yaml(["example.org."]),
    "duplicate (case-insensitive)": _yaml(["www.example.org", "WWW.example.org"]),
    "too many hosts": _yaml([f"h{n}.example.org" for n in range(MAX_HOSTS + 1)]),
    "no delay": _yaml(["a.example.org"], delay="0"),
    "delay as text": _yaml(["a.example.org"], delay="'3'"),
    "delay as boolean": _yaml(["a.example.org"], delay="true"),
    "timeout too long": _yaml(["a.example.org"], timeout="600"),
    "unknown key (port)": _yaml(["a.example.org"], extra="port: 8443\n"),
    "empty host list": _yaml([]), "not a mapping": b"- a.example.org\n",
}
BROKEN_VENDOR = {
    "vendor without hosts": VENDOR_YAML.replace("    hosts: [a.example.org, api.a.example"
                                                ".org]\n", ""),
    "duplicate vendor": VENDOR_YAML + "  - vendor: vendor a\n    hosts: [b.example.org]\n",
    "same host for two vendors": VENDOR_YAML + "  - vendor: B\n    hosts: [a.example.org]\n",
}
VALID_HOSTS = {"upper case (normalized)": (_yaml(["WWW.Example.ORG"]), False),
               "punycode name": (_yaml(["xn--bcher-kva.example"]), False),
               "byte-order mark": (b"\xef\xbb\xbf" + _yaml(["a.example.org"]), False),
               "vendor with two hosts": (VENDOR_YAML.encode(), True)}


def _rejects(raw: bytes, vendor_mode: bool) -> bool:
    try:
        parse_hosts(raw, "test", vendor_mode)
    except HostsError:
        return True
    return False


def hosts_checks() -> list[Check]:
    accepted = [k for k, raw in BROKEN_HOSTS.items() if not _rejects(raw, False)]
    accepted += [k for k, raw in BROKEN_VENDOR.items() if not _rejects(raw.encode(), True)]
    refused = [k for k, (raw, mode) in VALID_HOSTS.items() if _rejects(raw, mode)]
    demo = load_hosts(HOSTS_YAML)
    example = load_hosts(VENDOR_HOSTS_EXAMPLE, vendor_mode=True)
    tracker = SAMPLE_TRACKER.read_text(encoding="utf-8")
    n = len(BROKEN_HOSTS) + len(BROKEN_VENDOR)
    return [
        Check("TLS: malformed hosts files are rejected (scheme, port, path, IP, duplicate...)",
              not accepted, f"accepted: {accepted}" if accepted else f"{n}/{n} rejected"),
        Check("TLS: valid hosts files are accepted", not refused,
              f"refused: {refused}" if refused else f"{len(VALID_HOSTS)}/{len(VALID_HOSTS)}"),
        Check("TLS: the shipped demo list holds only public hostnames (no .invalid)",
              not any(t.host.endswith(".invalid") for t in demo.targets),
              f"{len(demo.targets)} hosts"),
        Check("TLS: the vendor example contacts nobody (.invalid only) and names tracker "
              "vendors", all(t.host.endswith(".invalid") and t.vendor in tracker
                             for t in example.targets), f"{len(example.targets)} hosts"),
    ]


# ---- the command and the loop --------------------------------------------------------------
def command_problems(argv: Sequence[str], host: str) -> list[str]:
    problems = [f"forbidden {a}" for a in argv if a in FORBIDDEN_OPTIONS]
    expected = ["s_client", "-connect", f"{host}:{PORT}", "-servername", host, "-groups",
                OFFERED_GROUPS, "-alpn", "h2,http/1.1", "-no_ign_eof"]
    if list(argv[1:]) != expected:
        problems.append("not the single plain handshake")
    if not OFFERED_GROUPS.startswith("*X25519MLKEM768:"):
        problems.append("hybrid not offered first with a key share")
    return problems


def command_checks() -> list[Check]:
    host = "www.example.org"
    argv = command("openssl", host)
    mutants = {"reconnect": argv + ["-reconnect"], "other port": [
        a.replace(":443", ":8443") for a in argv], "no SNI": argv[:5] + argv[7:]}
    missed = [k for k, m in mutants.items() if not command_problems(m, host)]
    return [
        Check("TLS: the command is one plain handshake to port 443 offering the hybrid, "
              "stdin the null device", not command_problems(argv, host)
              and RUN_OPTIONS["stdin"] is subprocess.DEVNULL, " ".join(argv[1:4])),
        Check("TLS: the command check catches reconnect, another port and missing SNI "
              "(control)", not missed, f"missed {missed}" if missed else f"{len(mutants)}/3"),
    ]


OK_TRANSCRIPT = "CONNECTED\n{pem}\nNegotiated TLS1.3 group: X25519MLKEM768\n" \
                "New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384\n"
Loop = Callable[..., list[Observation]]


def call_problems(loop: Loop, pem: str) -> list[str]:
    cfg = parse_hosts(_yaml(["a.example.org", "b.example.org", "c.example.org"]), "test")
    events: list[tuple] = []

    def runner(argv, timeout):
        events.append(("call", argv[3]))
        if argv[3].startswith("b."):
            return 1, "connect:errno=10061"
        return 0, OK_TRANSCRIPT.format(pem=pem)

    observations = loop(cfg, "openssl", "OpenSSL test", runner,
                        lambda s: events.append(("sleep", s)), lambda: STAMP)
    want = [("call", "a.example.org:443"), ("sleep", 3), ("call", "b.example.org:443"),
            ("sleep", 3), ("call", "c.example.org:443")]
    problems = [] if events == want else [f"events {events}"]
    if [o.status for o in observations] != ["ok", "failed", "ok"]:
        problems.append("statuses " + str([o.status for o in observations]))
    return problems


def _retrying(cfg, openssl, version, runner, sleep, clock) -> list[Observation]:
    """A plausible 'improvement' the rules forbid: retry a failed host once."""
    out = []
    for o in check_hosts(cfg, openssl, version, runner, sleep, clock):
        if o.status != "ok":
            runner(command(openssl, o.host), cfg.timeout_seconds)
        out.append(o)
    return out


# ---- parser and classifier ------------------------------------------------------------------
def make_cert(key) -> tuple[str, str]:
    """(PEM, SHA-256 of DER) of a one-day self-signed certificate for localhost."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    algorithm = None if isinstance(key, ed25519.Ed25519PrivateKey) else hashes.SHA256()
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1).not_valid_before(now)
            .not_valid_after(now + timedelta(days=1)).sign(key, algorithm))
    der = cert.public_bytes(serialization.Encoding.DER)
    return cert.public_bytes(serialization.Encoding.PEM).decode(), hashlib.sha256(der).hexdigest()


# (cipher line, group line, with certificate?, return code) -> expected fields, by hand
TRANSCRIPTS = {
    "hybrid, OpenSSL 3.5+ format": ("New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384",
        "Negotiated TLS1.3 group: X25519MLKEM768", True, 0,
        ("ok", "TLSv1.3", "X25519MLKEM768", HYBRID, "")),
    "classical X25519": ("New, TLSv1.3, Cipher is TLS_AES_128_GCM_SHA256",
        "Peer Temp Key: X25519, 253 bits", True, 0, ("ok", "TLSv1.3", "X25519", CLASSICAL, "")),
    "classical ECDH curve": ("New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384",
        "Peer Temp Key: ECDH, secp384r1, 384 bits", True, 0,
        ("ok", "TLSv1.3", "secp384r1", CLASSICAL, "")),
    "TLS 1.2, older label": ("New, TLSv1.2, Cipher is ECDHE-RSA-AES128-GCM-SHA256",
        "Server Temp Key: ECDH, prime256v1, 256 bits", True, 0,
        ("ok", "TLSv1.2", "prime256v1", CLASSICAL, "")),
    "TLS 1.2 finite-field DH": ("New, TLSv1.2, Cipher is DHE-RSA-AES256-GCM-SHA384",
        "Server Temp Key: DH, 2048 bits", True, 0,
        ("ok", "TLSv1.2", "DH 2048 bits", CLASSICAL, "")),
    "TLS 1.3, group line missing": ("New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384", "",
        True, 0, ("ok", "TLSv1.3", "", UNKNOWN, "")),
    # TLS 1.2 has no hybrid groups: static-RSA key exchange prints no group, still classical
    "TLS 1.2, group line missing": ("New, TLSv1.2, Cipher is AES256-GCM-SHA384", "", True, 0,
        ("ok", "TLSv1.2", "", CLASSICAL, "")),
    "unrecognized group": ("New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384",
        "Negotiated TLS1.3 group: FutureGroup9", True, 0,
        ("ok", "TLSv1.3", "FutureGroup9", UNKNOWN, "")),
    "connection refused": ("", "connect:errno=10061", False, 1,
        ("failed", "", "", "", "connection failed")),
    "name lookup": ("", "BIO_lookup_ex:system lib", False, 1,
        ("failed", "", "", "", "name lookup failed")),
    "protocol alert": ("New, (NONE), Cipher is (NONE)", "error:0A00042E:SSL routines:"
        "ssl3_read_bytes:tlsv1 alert protocol version:rec_layer_s3.c:918", False, 1,
        ("failed", "", "", "", "handshake refused (alert: protocol version)")),
    "timed out": ("", "", False, None, ("failed", "", "", "", "timed out")),
    "cipher but no certificate": ("New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384",
        "Negotiated TLS1.3 group: X25519MLKEM768", False, 0,
        ("failed", "", "", "", "handshake did not complete")),
}
GROUPS = {"X25519MLKEM768": HYBRID, "SecP256r1MLKEM768": HYBRID, "SecP384r1MLKEM1024": HYBRID,
          "MLKEM768": PQC_ONLY, "X25519": CLASSICAL, "secp256r1": CLASSICAL,
          "prime256v1": CLASSICAL, "ffdhe2048": CLASSICAL, "DH 2048 bits": CLASSICAL,
          "": UNKNOWN, "X25519Kyber768Draft00": UNKNOWN}


def _missing_as_classical(group: str) -> str:
    """The blind-verification fault: a group the transcript did not show read as classical."""
    return group_class(group) if group else CLASSICAL


def parser_checks() -> list[Check]:
    ec_pem, ec_sha = make_cert(ec.generate_private_key(ec.SECP256R1()))
    rsa_pem, rsa_sha = make_cert(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    ed_pem, ed_sha = make_cert(ed25519.Ed25519PrivateKey.generate())
    wrong = []
    for label, (cipher, group, has_cert, rc, want) in TRANSCRIPTS.items():
        text = "\n".join(["CONNECTED", ec_pem if has_cert else "", group, cipher])
        o = parse_transcript(text, rc, Target("t.example.org"), STAMP, "test", "0" * 64)
        if (o.status, o.tls_version, o.key_exchange_group, o.key_exchange_class,
                o.error) != want:
            wrong.append(label)
    certs = {"EC P-256": (ec_pem, ("EC secp256r1", 256, "ecdsa-with-SHA256", ec_sha)),
             "RSA 2048": (rsa_pem, ("RSA", 2048, "sha256WithRSAEncryption", rsa_sha)),
             "Ed25519": (ed_pem, ("Ed25519", 256, "ed25519", ed_sha))}
    bad_certs = [k for k, (pem, want) in certs.items() if cert_facts(pem) != want]
    garbled = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"
    if cert_facts(garbled) != (UNPARSEABLE, None, UNPARSEABLE,
                               hashlib.sha256(b"\x00\x00\x00").hexdigest()):
        bad_certs.append("unparseable certificate")
    bad_groups = [g for g, want in GROUPS.items() if group_class(g) != want]
    caught = [g for g, want in GROUPS.items() if _missing_as_classical(g) != want]
    return [
        Check("TLS: transcripts in every OpenSSL format parse to the hand-worked fields, "
              "failures included", not wrong, f"wrong: {wrong}" if wrong
              else f"{len(TRANSCRIPTS)} transcripts"),
        Check("TLS: certificate key type, size, signature and SHA-256 match the generated "
              "certificates; an unparseable one is recorded, not fatal", not bad_certs,
              f"wrong: {bad_certs}" if bad_certs else "3 keys and 1 garbled"),
        Check("TLS: group names classify as RFC 10024 / IANA say; unseen or unknown groups "
              "are never classical", not bad_groups, f"wrong: {bad_groups}" if bad_groups
              else f"{len(GROUPS)} names"),
        Check("TLS: the classifier check catches a rule that reads a missing group as "
              "classical (control)", caught == [""], f"caught {caught}"),
        Check("TLS: one call per host in order, a pause between hosts, failures not retried",
              not call_problems(check_hosts, ec_pem), "; ".join(call_problems(check_hosts,
                                                                             ec_pem))),
        Check("TLS: the call check catches a loop that retries a failed host (control)",
              bool(call_problems(_retrying, ec_pem))),
    ]


# ---- live handshake to a local test server ------------------------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def local_handshake(exe: str, version: str, groups: str, key) -> tuple[Observation, str]:
    """Start `openssl s_server` on 127.0.0.1 offering only `groups`, handshake once."""
    pem, sha = make_cert(key)
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = Path(tmp) / "cert.pem", Path(tmp) / "key.pem"
        cert_path.write_text(pem, encoding="ascii")
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        port = _free_port()
        server = subprocess.Popen(
            [exe, "s_server", "-accept", f"127.0.0.1:{port}", "-cert", str(cert_path),
             "-key", str(key_path), "-groups", groups, "-naccept", "1", "-www"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        guard = threading.Timer(20, server.kill)
        guard.start()
        try:
            for line in server.stdout:
                if "ACCEPT" in line:
                    break
            rc, text = run_openssl(command(exe, "localhost", port, "127.0.0.1"), 15)
        finally:
            guard.cancel()
            server.kill()
            server.wait()
    return parse_transcript(text, rc, Target("localhost"), STAMP, version, "0" * 64,
                            port), sha


def live_checks() -> list[Check]:
    name = "TLS: live handshakes to a local test server give the configured group and cert"
    found = find_openssl()
    if found is None:
        return [not_run(name, "no OpenSSL build that can offer X25519MLKEM768 was found")]
    exe, version = found
    cases = {"hybrid only": ("X25519MLKEM768", ec.generate_private_key(ec.SECP256R1()),
                             ("ok", "TLSv1.3", "X25519MLKEM768", HYBRID, "EC secp256r1", 256)),
             "X25519 only": ("X25519", rsa.generate_private_key(public_exponent=65537,
                                                               key_size=2048),
                             ("ok", "TLSv1.3", "X25519", CLASSICAL, "RSA", 2048))}
    wrong = []
    for label, (groups, key, want) in cases.items():
        o, sha = local_handshake(exe, version, groups, key)
        got = (o.status, o.tls_version, o.key_exchange_group, o.key_exchange_class,
               o.cert_key_type, o.cert_key_bits)
        if got != want or o.cert_sha256 != sha:
            wrong.append(f"{label}: {got}{'' if o.cert_sha256 == sha else ', cert hash'}")
    return [Check(name, not wrong, "; ".join(wrong) if wrong
                  else f"{len(cases)} servers, {version.split(' (')[0]}")]


# ---- evidence files --------------------------------------------------------------------------
def evidence_problems(observations: Sequence[Observation]) -> list[str]:
    problems = []
    for o in observations:
        if o.status == "ok":
            problems += [f"{o.host}: no {f}" for f in EVIDENCE_FIELDS if not getattr(o, f)]
            if o.key_exchange_class != exchange_class(o.tls_version, o.key_exchange_group):
                problems.append(f"{o.host}: class does not follow from the group")
            if o.offered_groups != OFFERED_GROUPS:
                problems.append(f"{o.host}: hybrid not offered ({o.offered_groups})")
        elif o.status != "failed" or not o.error:
            problems.append(f"{o.host}: failed without a reason")
    return problems


def demo_checks() -> tuple[list[Observation] | None, list[Check]]:
    name = "TLS: demo evidence covers each listed host once, complete, paced, current"
    if not DEMO_OBSERVATIONS.is_file():
        return None, [not_run(name, f"{DEMO_OBSERVATIONS.name} absent; run tls_check.py")]
    cfg = load_hosts(HOSTS_YAML)
    try:
        doc, observations = load_observations(DEMO_OBSERVATIONS)
    except ObservationsError as exc:
        return None, [not_run(name, str(exc))]
    if doc.get("hosts_config_sha256") != cfg.sha256:
        return None, [not_run(name, "tls_hosts.yaml changed since the demo ran")]
    if doc.get("tool_version") != TOOL_VERSION:
        return None, [not_run(name, f"recorded by tool {doc.get('tool_version')}, now "
                                    f"{TOOL_VERSION}; rerun tls_check.py")]
    problems = evidence_problems(observations)
    if [o.host for o in observations] != [t.host for t in cfg.targets]:
        problems.append("hosts differ from tls_hosts.yaml (or repeat)")
    times = [datetime.fromisoformat(o.observed_at) for o in observations]
    if any((b - a).total_seconds() < cfg.delay_seconds for a, b in zip(times, times[1:])):
        problems.append(f"handshakes closer than {cfg.delay_seconds} s")
    if any(o.port != PORT or o.hosts_config_sha256 != cfg.sha256 for o in observations):
        problems.append("a row's port or hosts hash differs")
    return observations, [Check(name, not problems, "; ".join(problems[:3]) if problems
                                else f"{len(observations)} hosts")]


def ignore_checks() -> list[Check]:
    name = "TLS: real-vendor files (*.local.*) are gitignored and none is tracked"
    local, shipped = git_ignored(VENDOR_HOSTS), git_ignored(HOSTS_YAML)
    if local is None or shipped is None:
        return [not_run(name, "git could not be asked")]
    tracked = subprocess.run(["git", "-C", str(HERE), "ls-files", "*.local.*"],
                             capture_output=True, text=True).stdout.split()
    return [Check(name, local and not shipped and not tracked,
                  f"local ignored {local}, demo list ignored {shipped}, tracked {tracked}")]


# ---- the stage run_all.py calls ---------------------------------------------------------------
def private_run(tracker: Path, vendor_obs: Path | None) -> bool:
    """Real vendor data in play (a custom tracker, or vendor handshakes): every output
    that names vendors or their hosts goes to a gitignored *.local.* file."""
    return vendor_obs is not None or tracker.resolve() != SAMPLE_TRACKER.resolve()


def part9_tls(tracker: Path, vendors: Sequence[Vendor], vendor_obs: Path | None
              ) -> tuple[list[VendorClaim], list[Observation] | None, Path, list[Check]]:
    section("PART 9 -- TLS POST-QUANTUM CHECK (vendor claims vs observed handshakes)")
    observations = load_vendor_observations(vendor_obs, vendors) if vendor_obs else []
    claims = compare(vendors, observations)
    out = OBSERVED_TRACKER_LOCAL if private_run(tracker, vendor_obs) else OBSERVED_TRACKER
    write_observed_tracker(out, tracker, claims)
    demo, checks = demo_checks()
    if demo:
        print("  Demo (public endpoints, one handshake each, as recorded):")
        print_observations(demo)
    source = f"from {vendor_obs.name}" if vendor_obs else "no vendor observations loaded"
    print(f"\n  Vendor claims ({source}):")
    for c in claims:
        print(f"    {c.vendor:<40} claimed {c.claimed:<8} observed {c.observed:<13} "
              f"{c.outcome}")
    checks += hosts_checks() + command_checks() + parser_checks() + live_checks()
    checks += ignore_checks() + claim_checks()
    if vendor_obs:
        name = "TLS: every loaded vendor observation carries its evidence, from this tool"
        problems = evidence_problems(observations) + [
            f"{o.host}: recorded by tool {o.tool_version}" for o in observations
            if o.tool_version != TOOL_VERSION]
        checks.append(not_run(name, f"{vendor_obs.name} holds no observations")
                      if not observations else
                      Check(name, not problems, "; ".join(problems[:3])))
    return claims, demo, out, checks
