"""CycloneDX 1.7 CBOM: the crypto inventory as a machine-readable bill of materials.

CycloneDX (ECMA-424) models cryptography as components of type "cryptographic-asset".
This export holds:
    one component per distinct algorithm, protocol or key size the scanner saw, with
        every occurrence (file and line) as evidence;
    one "file" component per scanned file that has findings, with its SHA-256;
    dependencies: each file depends on the crypto assets found in it.
Classification and priority come along as namespaced properties ("pqc-readiness:*"),
because they are this toolkit's judgments, not facts CycloneDX defines.

WHAT THE EXPORT DOES NOT CLAIM
------------------------------
The scanner sees names in text. Where the name does not say how an algorithm is used,
the component says so instead of guessing: RSA is recorded with primitive "unknown"
(the schema's RSA families are scheme-specific, such as RSAES-OAEP or RSASSA-PSS, and
the text does not say which), and a bare curve name is "elliptic-curve (use unknown)".

Validated against the official 1.7.2 schemas vendored in schemas/cyclonedx-1.7.2/.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource

from common import HERE, Check, not_run, section
from migration_register import RegisterRow, RunMeta

SCHEMA_DIR = HERE / "schemas" / "cyclonedx-1.7.2"
SCHEMA_FILES = ("bom-1.7.schema.json", "spdx.schema.json", "jsf-0.82.schema.json",
                "cryptography-defs.schema.json")
NS = "pqc-readiness"


def _alg(name: str, primitive: str, family: str | None = None, level: int | None = None,
         param: str | None = None) -> tuple[str, dict]:
    props: dict = {"primitive": primitive}
    if family:
        props["algorithmFamily"] = family
    if param:
        props["parameterSetIdentifier"] = param
    if level is not None:
        props["nistQuantumSecurityLevel"] = level
    return name, {"assetType": "algorithm", "algorithmProperties": props}


def _protocol(name: str, kind: str, version: str | None) -> tuple[str, dict]:
    props: dict = {"type": kind}
    if version:
        props["version"] = version
    return name, {"assetType": "protocol", "protocolProperties": props}


def _ecc(m: str) -> tuple[str, dict]:
    if m.startswith(("ecdsa", "withecdsa", "es2", "es3", "es5")):
        return _alg("ECDSA", "signature", "ECDSA", 0)
    if m.startswith(("eddsa", "ed25519", "ed448")):
        return _alg("EdDSA", "signature", "EdDSA", 0)
    if m.startswith(("ecdh", "x25519", "curve25519", "x448")):
        return _alg("ECDH", "key-agree", "ECDH", 0)
    return _alg("Elliptic-curve cryptography (use unknown)", "unknown", None, 0)


# Hybrid key-exchange groups by their exact name. NIST security category comes from the
# ML-KEM parameter set (768 -> 3, 1024 -> 5, FIPS 203). X25519Kyber768 is the
# PRE-STANDARD draft group, so no FIPS category is claimed for it.
HYBRIDS = {"x25519mlkem768": 3, "secp256r1mlkem768": 3, "secp384r1mlkem1024": 5,
           "x25519kyber768": None}


def _hybrid(matched: str) -> tuple[str, dict]:
    level = HYBRIDS[matched.lower()]
    name = matched if level is not None else f"{matched} (pre-standard Kyber draft)"
    return _alg(name, "kem", None, level)


def _tls(m: str) -> tuple[str, dict]:
    if m.startswith("ssl"):
        return _protocol("SSL (legacy)", "other", None)
    if "elbsecuritypolicy" in m:
        return _protocol("TLS policy allowing 1.0", "tls", None)
    version = "1.1" if re.search(r"1[._-]1$", m) else "1.0"
    return _protocol(f"TLS {version}", "tls", version)


def identity(row: RegisterRow) -> tuple[str, dict]:
    """(component name, cryptoProperties) for one finding, from its rule and matched text."""
    m, rule = row.matched_text.lower(), row.rule_id
    if rule in ("QV-KEYSIZE", "CW-KEYSIZE"):
        bits = int(re.findall(r"\d{3,5}", m)[-1])
        return (f"{bits}-bit key (algorithm not identified)",
                {"assetType": "related-crypto-material",
                 "relatedCryptoMaterialProperties": {"type": "key", "size": bits}})
    fixed = {
        "QV-RSA": ("RSA (scheme not identified)", "unknown", None, 0),
        "QV-DH": ("Finite-field Diffie-Hellman", "key-agree", "FFDH", 0),
        "QV-DSA": ("DSA", "signature", "DSA", 0),
        "CW-MD5": ("MD5", "hash", "MD5", None),
        "CW-SHA1": ("SHA-1", "hash", "SHA-1", None),
        "PQ-KEM": ("ML-KEM", "kem", "ML-KEM", None),
        "PQ-SYMMETRIC": ("AES-256", "block-cipher", "AES", 5),
    }
    if rule in fixed:
        name, primitive, family, level = fixed[rule]
        return _alg(name, primitive, family, level, "256" if rule == "PQ-SYMMETRIC" else None)
    if rule == "PQ-HYBRID":
        return _hybrid(row.matched_text)
    if rule == "QV-ECC":
        return _ecc(m)
    if rule == "CW-TLS-LEGACY":
        return _tls(m)
    if rule == "CW-CIPHER":
        if m.startswith(("rc4", "arcfour")):
            return _alg("RC4", "stream-cipher", "RC4")
        single = m == "des" or m.startswith("des-cbc") or m.startswith("des_")
        return _alg("DES", "block-cipher", "DES") if single else _alg("3DES", "block-cipher", "3DES")
    if rule == "PQ-SIGNATURE":
        return (_alg("SLH-DSA", "signature", "SLH-DSA") if m.startswith("slh")
                else _alg("ML-DSA", "signature", "ML-DSA"))
    if rule == "PQ-HASH":                   # matched text is SHA-384, SHA512, SHA3 or SHA-3
        if re.fullmatch(r"sha-?3", m):
            return _alg("SHA-3", "hash", "SHA-3")
        bits = re.findall(r"\d{3}", m)[0]
        return _alg(f"SHA-{bits}", "hash", "SHA-2", None, bits)
    raise ValueError(f"no CBOM identity for rule {rule}")


def _ref(prefix: str, name: str) -> str:
    return f"{prefix}:" + re.sub(r"[^a-z0-9.+-]+", "-", name.lower()).strip("-")


def _crypto_components(rows: list[RegisterRow]):
    assets: dict[str, dict] = {}
    uses: dict[str, set[str]] = defaultdict(set)
    for r in sorted(rows, key=lambda r: (r.file, r.line, r.rule_id)):
        name, props = identity(r)
        ref = _ref("crypto", name)
        comp = assets.setdefault(ref, {
            "type": "cryptographic-asset", "bom-ref": ref, "name": name,
            "cryptoProperties": props, "evidence": {"occurrences": []},
            "properties": [{"name": f"{NS}:category", "value": r.category}]})
        comp["evidence"]["occurrences"].append(
            {"location": r.file, "line": r.line, "additionalContext":
             f"{r.rule_id}: matched '{r.matched_text}'"})
        uses[r.file].add(ref)
    return list(assets.values()), uses


def _file_components(rows: list[RegisterRow], levels: tuple[str, ...]) -> list[dict]:
    by_file: dict[str, list[RegisterRow]] = defaultdict(list)
    for r in rows:
        by_file[r.file].append(r)
    out = []
    for path, rs in sorted(by_file.items()):
        top = min((r.priority for r in rs), key=levels.index)   # levels run most urgent first
        out.append({"type": "file", "bom-ref": _ref("file", path), "name": path,
                    "hashes": [{"alg": "SHA-256", "content": rs[0].file_sha256}],
                    "properties": [
                        {"name": f"{NS}:asset-ids", "value": rs[0].asset_ids or "none"},
                        {"name": f"{NS}:classification", "value": rs[0].classification},
                        {"name": f"{NS}:highest-priority", "value": top}]})
    return out


def build_cbom(rows: list[RegisterRow], meta: RunMeta, levels: tuple[str, ...]) -> dict:
    crypto, uses = _crypto_components(rows)
    files = _file_components(rows, levels)
    # CycloneDX asks for a unique serial per BOM. Deriving it from the run's timestamp and
    # inputs keeps it unique per run yet reproducible when the same run is rebuilt.
    seed = "|".join([meta.scan_timestamp, meta.tool_version, meta.config_sha256,
                     meta.register_sha256]
                    + sorted(f"{f['name']}={f['hashes'][0]['content']}" for f in files))
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.7.schema.json",
        "bomFormat": "CycloneDX", "specVersion": "1.7",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, seed)}", "version": 1,
        "metadata": {
            "timestamp": meta.scan_timestamp,
            "tools": {"components": [{"type": "application", "name": "pqc-readiness-toolkit",
                                      "version": meta.tool_version}]},
            "component": {"type": "application", "bom-ref": "scan-root",
                          "name": meta.scan_root},
            "properties": [{"name": f"{NS}:config-sha256", "value": meta.config_sha256},
                           {"name": f"{NS}:register-sha256", "value": meta.register_sha256},
                           {"name": f"{NS}:note", "value": "Synthetic sample; names seen in "
                            "text, not verified deployments."}]},
        "components": crypto + files,
        "dependencies": [{"ref": "scan-root", "dependsOn": [f["bom-ref"] for f in files]}]
        + [{"ref": f["bom-ref"], "dependsOn": sorted(uses[f["name"]])} for f in files]
        + [{"ref": c["bom-ref"]} for c in crypto],
    }


def validator() -> Draft7Validator | None:
    """A validator for bom-1.7 with its referenced schemas, or None if they are missing."""
    if not all((SCHEMA_DIR / f).is_file() for f in SCHEMA_FILES):
        return None
    schemas = {f: json.loads((SCHEMA_DIR / f).read_text(encoding="utf-8")) for f in SCHEMA_FILES}
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values())
    return Draft7Validator(schemas["bom-1.7.schema.json"], registry=registry,
                           format_checker=FormatChecker())


def write_cbom(path: Path, bom: dict) -> None:
    path.write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8")


# ======================================================================================
# SELF-CHECKS
# ======================================================================================
def cbom_checks(bom: dict, rows: list[RegisterRow], meta: RunMeta,
                levels: tuple[str, ...]) -> list[Check]:
    v = validator()
    name = "CBOM: validates against the official CycloneDX 1.7.2 schema"
    if v is None:
        checks = [not_run(name, f"schemas missing from {SCHEMA_DIR.name}/")]
    else:
        errors = sorted(v.iter_errors(bom), key=lambda e: list(e.path))
        # Controls: an invalid value in the main schema (asset type) and one defined only
        # in a referenced schema (algorithm family, cryptography-defs) must both fail, which
        # proves the external $refs are really resolved.
        bad_type, bad_family = json.loads(json.dumps(bom)), json.loads(json.dumps(bom))
        bad_type["components"][0]["cryptoProperties"]["assetType"] = "algorythm"
        fam = next(c for c in bad_family["components"] if "algorithmFamily" in
                   c.get("cryptoProperties", {}).get("algorithmProperties", {}))
        fam["cryptoProperties"]["algorithmProperties"]["algorithmFamily"] = "RSA-ish"
        checks = [Check(name, not errors, f"{len(errors)} errors"
                        + (f"; first: {errors[0].message[:80]}" if errors else "")),
                  Check("CBOM: the validator rejects a bad asset type and a bad algorithm "
                        "family from the referenced schema (controls)",
                        not v.is_valid(bad_type) and not v.is_valid(bad_family))]
    crypto = [c for c in bom["components"] if c["type"] == "cryptographic-asset"]
    occurrences = sorted((o["location"], o["line"]) for c in crypto
                         for o in c["evidence"]["occurrences"])
    file_hashes = {c["name"]: c["hashes"][0]["content"] for c in bom["components"]
                   if c["type"] == "file"}
    refs = [c["bom-ref"] for c in bom["components"]] + ["scan-root"]
    dangling = [d for dep in bom["dependencies"] for d in [dep["ref"], *dep.get("dependsOn", [])]
                if d not in refs]
    rsa = [c for c in crypto if c["name"].startswith("RSA")]
    return checks + [
        Check("CBOM: every register row appears exactly once as an occurrence",
              occurrences == sorted((r.file, r.line) for r in rows), f"{len(occurrences)}"),
        Check("CBOM: file hashes equal the register's evidence hashes",
              file_hashes == {r.file: r.file_sha256 for r in rows}, f"{len(file_hashes)} files"),
        Check("CBOM: bom-refs are unique and every dependency resolves",
              len(refs) == len(set(refs)) and not dangling,
              f"dangling {dangling}" if dangling else f"{len(refs)} refs"),
        Check("CBOM: RSA is not given a scheme the scanner cannot see",
              bool(rsa) and all("algorithmFamily" not in c["cryptoProperties"]
                                .get("algorithmProperties", {}) for c in rsa)),
        Check("CBOM: rebuilding from the same rows gives an identical document",
              build_cbom(rows, meta, levels) == bom),
    ]


def part8_cbom(rows: list[RegisterRow], meta: RunMeta, levels: tuple[str, ...],
               path: Path) -> tuple[dict, list[Check]]:
    section("PART 8 -- CYCLONEDX 1.7 CBOM (crypto inventory as a bill of materials)")
    bom = build_cbom(rows, meta, levels)
    write_cbom(path, bom)
    crypto = [c for c in bom["components"] if c["type"] == "cryptographic-asset"]
    print(f"  {len(crypto)} cryptographic assets, "
          f"{len(bom['components']) - len(crypto)} files -> {path.name}")
    for c in crypto:
        print(f"    {len(c['evidence']['occurrences']):>2} x {c['name']}")
    return bom, cbom_checks(bom, rows, meta, levels)
