"""Read application credentials from process env or systemd LoadCredential files.

The VPS uses explicit ``CREDENTIALS_MODE=file``. Values are read on demand;
the provider never logs or caches them and never falls back to env.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path


class CredentialError(RuntimeError):
    """Safe-to-display configuration or credential retrieval error."""


_FILES = {
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "OPENAI_API_KEY": "openai_api_key",
    "DASHSCOPE_API_KEY": "dashscope_api_key",
    "SMTP_AUTH_CODE": "smtp_auth_code",
    "SMTP_USERNAME": "sender_email",
    "MAIL_FROM": "sender_email",
    "MAIL_TO": "recipient_email",
}
_EMAIL_NAMES = {"SMTP_USERNAME", "MAIL_FROM", "MAIL_TO"}
_UNCONFIGURED_MODEL_CREDENTIAL = "__NEWSDaily_UNCONFIGURED_MODEL_CREDENTIAL__"


class CredentialProvider:
    """One interface for Windows process env and Debian systemd credentials."""

    def __init__(
        self,
        mode: str | None = None,
        environ: Mapping[str, str] | None = None,
        *,
        credential_dir: str | os.PathLike[str] | None = None,
    ):
        self.environ = os.environ if environ is None else environ
        selected = mode if mode is not None else self.environ.get("CREDENTIALS_MODE", "env")
        self.mode = selected.strip().lower()
        if self.mode not in {"env", "file"}:
            raise CredentialError("CREDENTIALS_MODE must be either 'env' or 'file'")
        self._credential_dir = credential_dir

    def _path(self, name: str) -> Path:
        directory = self._credential_dir or self.environ.get("CREDENTIALS_DIRECTORY")
        if not directory:
            raise CredentialError("File credential mode requires systemd CREDENTIALS_DIRECTORY")
        return Path(directory) / _FILES[name]

    def is_configured(self, name: str) -> bool:
        if name not in _FILES:
            raise CredentialError("Unsupported application credential")
        try:
            self.get(name)
            return True
        except CredentialError:
            return False

    def get(self, name: str) -> str:
        if name not in _FILES:
            raise CredentialError("Unsupported application credential")
        if self.mode == "env":
            value = self.environ.get(name)
            if not value:
                raise CredentialError(f"Missing required environment credential: {name}")
        else:
            path = self._path(name)
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise CredentialError(f"Credential file for {name} must be a regular file")
                # Explicit directories are source files and must remain private.
                # systemd controls access to its per-unit CREDENTIALS_DIRECTORY;
                # LoadCredential copies may have group/other mode bits set.
                if self._credential_dir is not None and os.name != "nt" and info.st_mode & 0o077:
                    raise CredentialError(f"Credential file for {name} has unsafe permissions")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                try:
                    content = os.read(descriptor, 4097)
                finally:
                    os.close(descriptor)
                if len(content) > 4096:
                    raise CredentialError(f"Credential file for {name} is too large")
                value = content.decode("utf-8", errors="strict").rstrip("\r\n")
            except CredentialError:
                raise
            except (OSError, UnicodeError):
                raise CredentialError(f"Could not read valid UTF-8 credential file for {name}") from None
            if value == _UNCONFIGURED_MODEL_CREDENTIAL:
                raise CredentialError(f"Missing required file credential: {name}")
        if not value or value != value.strip() or "\n" in value or "\r" in value or "\x00" in value:
            raise CredentialError(f"Credential {name} is empty or contains invalid whitespace")
        if name in _EMAIL_NAMES and (value.count("@") != 1 or any(char.isspace() or char in ",;<>" for char in value)):
            raise CredentialError(f"Credential {name} is not a single email address")
        return value


def get_credential(name: str) -> str:
    return CredentialProvider().get(name)
