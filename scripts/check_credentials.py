"""Check selected model and mail credentials without printing values."""

import json
from pathlib import Path
from app.credentials import CredentialProvider
from app.llm import configured_client
from app.pipeline import _model_profile


def main() -> None:
    provider = CredentialProvider()
    settings=json.loads((Path(__file__).resolve().parent.parent/"config/settings.json").read_text(encoding="utf-8"))
    _model_profile(settings)
    model_credential=configured_client(settings,provider).credential
    for name in (model_credential, "SMTP_AUTH_CODE", "MAIL_FROM", "MAIL_TO"):
        provider.get(name)
    print("Selected model and mail credentials are available")


if __name__ == "__main__":
    main()
