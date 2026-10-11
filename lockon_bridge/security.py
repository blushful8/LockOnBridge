"""Local pairing identity: TLS pin + bearer token, provisioned out of band in the UI."""
from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from .paths import data_root

_lock = threading.RLock()


def security_root() -> Path:
    override = os.environ.get("LOCKON_BRIDGE_SECURITY_DIR")
    if override:
        return Path(override)
    if Path("D:/").is_dir():
        return Path("D:/CodexData/LockOnBridge/security")
    return data_root() / "security"


def _protect(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        return data  # POSIX files are created with mode 0600 below.
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    api = ctypes.windll.crypt32
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def identity() -> dict[str, str]:
    with _lock:
        root = security_root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / "identity.dpapi"
        if path.exists():
            # Fail closed on corruption; never silently rotate an existing phone pairing.
            result = json.loads(_protect(path.read_bytes(), decrypt=True))
            cert = x509.load_pem_x509_certificate(result["certificate"].encode())
            result["fingerprint"] = cert.fingerprint(hashes.SHA256()).hex()
            if not isinstance(result["token"], str) or len(result["token"]) != 64:
                raise ValueError("Invalid Bridge pairing identity")
            return result
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LockOn Bridge paired PC")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=5))
                .not_valid_after(now + dt.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(key, hashes.SHA256()))
        result = {
            "certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "private_key": key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
            "token": secrets.token_hex(32),
            "fingerprint": cert.fingerprint(hashes.SHA256()).hex(),
        }
        fd, temp = tempfile.mkstemp(dir=root, prefix="identity-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(_protect(json.dumps(result).encode()))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return result


def pairing_code() -> str:
    material = identity()
    return "LO1." + material["fingerprint"] + "." + material["token"]


def tls_context(material: dict[str, str]) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    fd, path = tempfile.mkstemp(dir=security_root(), prefix="tls-", suffix=".pem")
    try:
        if os.name == "nt":
            # Restrict the empty file BEFORE any decrypted key material is written.
            account = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
            subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", account + ":F", "SYSTEM:F"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            fd = -1
            stream.write(material["certificate"] + material["private_key"])
        context.load_cert_chain(path)
    finally:
        if fd != -1:
            os.close(fd)
        os.unlink(path)
    return context
