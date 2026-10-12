"""Encrypt private data before it goes into a public repository.

The passphrase comes from the NUGGET_DATA_KEY secret. A key is derived from it
with scrypt (random salt per file) and the data is sealed with AES-256-GCM.
Without the passphrase the files are unreadable; publishing the passphrase later
reveals everything at once (and every file's SHA-256 was timestamped when written).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

ENV = "NUGGET_DATA_KEY"


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def passphrase() -> str | None:
    p = os.environ.get(ENV, "").strip()
    return p or None


def seal(data: bytes, pw: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(pw, salt)).encrypt(nonce, data, b"nugget-index/v1")
    return json.dumps({"v": 1, "kdf": "scrypt-n15-r8-p1", "cipher": "aes-256-gcm",
                       "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
                       "ct": base64.b64encode(ct).decode()}).encode()


def open_(blob: bytes, pw: str) -> bytes:
    o = json.loads(blob)
    return AESGCM(_key(pw, base64.b64decode(o["salt"]))).decrypt(base64.b64decode(o["nonce"]), base64.b64decode(o["ct"]), b"nugget-index/v1")


def write(path: Path, data: bytes, pw: str | None) -> Path:
    """Write sealed (path + '.enc') when a passphrase is set, otherwise plain."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if pw:
        out = path.with_name(path.name + ".enc")
        out.write_bytes(seal(data, pw))
    else:
        out = path
        out.write_bytes(data)
    return out


def read(path: Path, pw: str | None) -> bytes | None:
    enc = path.with_name(path.name + ".enc")
    if enc.exists():
        if not pw:
            raise SystemExit(f"{enc} is encrypted; set {ENV} to read it")
        return open_(enc.read_bytes(), pw)
    return path.read_bytes() if path.exists() else None


if __name__ == "__main__":
    # Decrypt a file for inspection: NUGGET_DATA_KEY=... python scripts/vault.py data/ledger.json
    import sys
    sys.stdout.buffer.write(read(Path(sys.argv[1]), passphrase()) or b"")
