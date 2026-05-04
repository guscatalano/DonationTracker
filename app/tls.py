"""Self-signed TLS cert helper.

Generates a 10-year self-signed cert covering localhost, 127.0.0.1, and every
non-loopback IPv4 address on this machine, so the same cert works whether you
hit the app from the host or from another device on the LAN.

Run standalone to print the key/cert paths (used by run.ps1 / run.sh):
    python -m app.tls --ensure
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
CERT_DIR = DATA_DIR / "certs"
KEY_PATH = CERT_DIR / "key.pem"
CERT_PATH = CERT_DIR / "cert.pem"


def _local_ips() -> list[str]:
    ips: set[str] = {"127.0.0.1"}
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None):
            ip = info[4][0]
            if "." in ip and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def _generate(key_path: Path, cert_path: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "donationtracker.local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Donation Tracker (self-signed)"),
    ])
    san_entries: list = [
        x509.DNSName("localhost"),
        x509.DNSName("donationtracker.local"),
    ]
    for ip in _local_ips():
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365 * 10))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass


def ensure_cert(key_path: Path = KEY_PATH, cert_path: Path = CERT_PATH) -> tuple[Path, Path]:
    if not (key_path.exists() and cert_path.exists()):
        _generate(key_path, cert_path)
    return key_path, cert_path


# Marker the self-signed cert puts in its Organization Name field. If the cert
# at CERT_PATH lacks this, it must have come from somewhere else (Let's Encrypt,
# mkcert, hand-placed PEM, etc.) — treat it as user-supplied.
SELF_SIGNED_ORG_MARKER = "Donation Tracker (self-signed)"


def is_self_signed_by_us(cert_path: Path = CERT_PATH) -> bool | None:
    """True if the cert was made by our generator, False if it came from
    elsewhere, None if there's no cert or we couldn't parse it."""
    if not cert_path.exists():
        return None
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        for attr in cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME):
            if SELF_SIGNED_ORG_MARKER in (attr.value or ""):
                return True
        return False
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if "--ensure" in sys.argv:
        k, c = ensure_cert()
        print(f"key={k}")
        print(f"cert={c}")
        print(f"sans={','.join(_local_ips())}", file=sys.stderr)
    else:
        print("usage: python -m app.tls --ensure")
