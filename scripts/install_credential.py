"""Interactively install one root-only VPS credential without shell history.

Run on Debian as root: ``sudo /opt/newsdaily/.venv/bin/python -B -m
scripts.install_credential deepseek_api_key``.  The value is prompted with echo
disabled and is never printed.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import stat
from pathlib import Path


NAMES = {"deepseek_api_key", "openai_api_key", "dashscope_api_key", "smtp_auth_code", "sender_email", "recipient_email"}
DEFAULT_DIRECTORY = Path("/etc/newsdaily/credentials")


def install_credential(name: str, value: str, directory: Path = DEFAULT_DIRECTORY) -> None:
    if name not in NAMES:
        raise ValueError("Unsupported credential name")
    if not value or value != value.strip() or any(char in value for char in "\r\n\x00"):
        raise ValueError("Credential value is empty or contains invalid whitespace")
    if name.endswith("_email") and (value.count("@") != 1 or any(char.isspace() or char in ",;<>" for char in value)):
        raise ValueError("Email credential must contain one address")
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
        raise PermissionError("Credential directory must be private (0700)")
    if os.name != "nt" and info.st_uid != 0:
        raise PermissionError("Credential directory must be owned by root")
    destination = directory / name
    temporary = directory / f".{name}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Install one private newsdaily credential")
    parser.add_argument("name", choices=sorted(NAMES))
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Run this installer as root on Debian")
    try:
        value = getpass.getpass(f"Enter {args.name} (input hidden): ")
        install_credential(args.name, value)
    except (OSError, ValueError, PermissionError):
        raise SystemExit("Credential was not installed; check value and private directory permissions") from None
    print(f"Credential installed: {args.name}")


if __name__ == "__main__":
    main()
