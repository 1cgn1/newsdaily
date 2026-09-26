import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.clock import beijing_now
from app.credentials import CredentialError, CredentialProvider
from app.delivery import SMTPMailer
from app.llm import DeepSeekClient
from app.privacy import recipient_binding
from scripts.install_credential import install_credential
from scripts.install_credential import NAMES as INSTALLABLE_CREDENTIALS


class StaticCredentials:
    def __init__(self, values):
        self.values = values

    def get(self, name):
        return self.values[name]


class CredentialProviderTests(unittest.TestCase):
    def test_manual_installer_supports_all_model_keys(self):
        self.assertTrue({"deepseek_api_key","openai_api_key","dashscope_api_key"}.issubset(INSTALLABLE_CREDENTIALS))
    def test_environment_mode_keeps_windows_process_environment(self):
        values = {
            "CREDENTIALS_MODE": "env",
            "DEEPSEEK_API_KEY": "fake-ds-key",
            "SMTP_AUTH_CODE": "fake-smtp-code",
            "MAIL_FROM": "sender@example.invalid",
            "SMTP_USERNAME": "sender@example.invalid",
            "MAIL_TO": "recipient@example.invalid",
        }
        provider = CredentialProvider(environ=values)
        for name in values.keys() - {"CREDENTIALS_MODE"}:
            self.assertEqual(provider.get(name), values[name])

    def test_explicit_file_mode_does_not_fallback_to_environment(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            provider = CredentialProvider(mode="file", environ={"DEEPSEEK_API_KEY": "must-not-fallback"}, credential_dir=directory)
            self.assertFalse(provider.is_configured("DEEPSEEK_API_KEY"))
            with self.assertRaises(CredentialError) as raised:
                provider.get("DEEPSEEK_API_KEY")
            self.assertNotIn("must-not-fallback", str(raised.exception))

    def test_empty_systemd_fallback_is_not_counted_as_configured(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            Path(directory,"openai_api_key").write_text("",encoding="utf-8")
            provider=CredentialProvider(mode="file",environ={"CREDENTIALS_DIRECTORY":directory})
            self.assertFalse(provider.is_configured("OPENAI_API_KEY"))

    def test_systemd_placeholder_cannot_be_used_as_a_model_key(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            Path(directory, "openai_api_key").write_text("__NEWSDaily_UNCONFIGURED_MODEL_CREDENTIAL__", encoding="utf-8")
            provider = CredentialProvider(mode="file", environ={"CREDENTIALS_DIRECTORY": directory})
            self.assertFalse(provider.is_configured("OPENAI_API_KEY"))
            with self.assertRaisesRegex(CredentialError, "Missing required file credential: OPENAI_API_KEY"):
                provider.get("OPENAI_API_KEY")

    def test_invalid_mode_and_missing_systemd_directory(self):
        with self.assertRaisesRegex(CredentialError, "CREDENTIALS_MODE"):
            CredentialProvider(mode="oci_vault")
        with self.assertRaisesRegex(CredentialError, "CREDENTIALS_DIRECTORY"):
            CredentialProvider(mode="file", environ={}).get("MAIL_TO")

    def test_four_credentials_read_separately_and_sender_alias(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            values = {
                "deepseek_api_key": "fake-ds-key",
                "smtp_auth_code": "fake-smtp-code",
                "sender_email": "sender@example.invalid",
                "recipient_email": "recipient@example.invalid",
            }
            for filename, value in values.items():
                path = Path(directory, filename)
                path.write_text(value + "\n", encoding="utf-8")
                path.chmod(0o600)
            provider = CredentialProvider(mode="file", credential_dir=directory, environ={"MAIL_TO": "wrong@example.invalid"})
            self.assertEqual(provider.get("DEEPSEEK_API_KEY"), values["deepseek_api_key"])
            self.assertEqual(provider.get("SMTP_AUTH_CODE"), values["smtp_auth_code"])
            self.assertEqual(provider.get("MAIL_FROM"), values["sender_email"])
            self.assertEqual(provider.get("SMTP_USERNAME"), values["sender_email"])
            self.assertEqual(provider.get("MAIL_TO"), values["recipient_email"])
            Path(directory, "recipient_email").write_text("new@example.invalid\n", encoding="utf-8")
            Path(directory, "recipient_email").chmod(0o600)
            self.assertEqual(provider.get("MAIL_TO"), "new@example.invalid")

    def test_invalid_file_content_and_symlink_fail_without_leaking_values(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            path = Path(directory, "deepseek_api_key")
            for content in (b"\xff", b"first\nsecond", b"X" * 4097):
                path.write_bytes(content)
                path.chmod(0o600)
                with self.assertRaises(CredentialError) as raised:
                    CredentialProvider(mode="file", credential_dir=directory).get("DEEPSEEK_API_KEY")
                self.assertNotIn("first", str(raised.exception))
            path.unlink()
            try:
                path.symlink_to(Path(directory, "outside"))
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(CredentialError):
                CredentialProvider(mode="file", credential_dir=directory).get("DEEPSEEK_API_KEY")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not enforced on Windows")
    def test_group_readable_file_is_rejected_on_linux(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            path = Path(directory, "smtp_auth_code")
            path.write_text("fake-auth", encoding="utf-8")
            path.chmod(0o640)
            with self.assertRaisesRegex(CredentialError, "unsafe permissions"):
                CredentialProvider(mode="file", credential_dir=directory).get("SMTP_AUTH_CODE")

    def test_systemd_managed_copy_does_not_require_source_file_mode(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            path = Path(directory, "deepseek_api_key")
            path.write_text("fake-ds-key\n", encoding="utf-8")
            path.chmod(0o644)
            provider = CredentialProvider(
                mode="file",
                environ={"CREDENTIALS_DIRECTORY": directory, "DEEPSEEK_API_KEY": "must-not-fallback"},
            )
            self.assertEqual(provider.get("DEEPSEEK_API_KEY"), "fake-ds-key")

    def test_errors_and_logs_never_contain_secret_or_email_values(self):
        sensitive = "recipient@example.invalid"
        logger = logging.getLogger("app.credentials.test")
        with self.assertLogs(logger, level="DEBUG") as captured:
            logger.debug("starting credential test")
            with tempfile.TemporaryDirectory(dir=".") as directory:
                Path(directory, "recipient_email").write_text(sensitive + "\nsecond-line", encoding="utf-8")
                with self.assertRaises(CredentialError) as raised:
                    CredentialProvider(mode="file", credential_dir=directory).get("MAIL_TO")
                self.assertNotIn(sensitive, str(raised.exception))
        self.assertNotIn(sensitive, "\n".join(captured.output))

    def test_deepseek_uses_shared_interface_without_network(self):
        fake_response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})
        call = {}
        def fake_post(*args, **kwargs):
            call.update(kwargs)
            return fake_response
        with patch.dict(sys.modules, {"httpx": SimpleNamespace(post=fake_post)}):
            result = DeepSeekClient(credentials=StaticCredentials({"DEEPSEEK_API_KEY": "fake-key"})).complete({"x": 1})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(call["headers"]["Authorization"], "Bearer fake-key")

    def test_deepseek_rejects_plain_http(self):
        with self.assertRaisesRegex(RuntimeError, "HTTPS URL"):
            DeepSeekClient(base_url="http://api.test.invalid", credentials=StaticCredentials({"DEEPSEEK_API_KEY": "fake"})).complete({})

    def test_smtp_uses_sender_file_and_sanitizes_server_error(self):
        credentials = StaticCredentials({"SMTP_AUTH_CODE": "fake-auth", "SMTP_USERNAME": "sender@example.invalid"})
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.test.invalid", "SMTP_PORT": "465", "MAIL_USE_SSL": "true"}), patch("app.delivery.smtplib.SMTP_SSL") as factory:
            smtp = factory.return_value.__enter__.return_value
            smtp.send_message.return_value = {}
            SMTPMailer(credentials=credentials).send("sender@example.invalid", "recipient@example.invalid", "subject", "<p>preview</p>", "preview")
            smtp.login.assert_called_once_with("sender@example.invalid", "fake-auth")
            smtp.login.side_effect = OSError("fake-auth recipient@example.invalid")
            with self.assertRaises(RuntimeError) as raised:
                SMTPMailer(credentials=credentials).send("sender@example.invalid", "recipient@example.invalid", "subject", "<p>preview</p>", "preview")
            self.assertNotIn("fake-auth", str(raised.exception))
            self.assertNotIn("recipient@example.invalid", str(raised.exception))

    def test_recipient_binding_does_not_contain_address_or_auth_code(self):
        binding = recipient_binding("2026-09-25", "recipient@example.invalid", "fake-auth")
        self.assertEqual(binding, recipient_binding("2026-09-25", "recipient@example.invalid", "fake-auth"))
        self.assertNotIn("recipient@example.invalid", binding)
        self.assertNotIn("fake-auth", binding)
        self.assertNotEqual(binding, recipient_binding("2026-09-25", "other@example.invalid", "fake-auth"))
        self.assertNotEqual(binding, recipient_binding("2026-09-25", "recipient@example.invalid", "rotated-auth"))

    def test_interactive_installer_writer_is_atomic_and_does_not_print_value(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            folder = Path(directory)
            folder.chmod(0o700)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                install_credential("sender_email", "sender@example.invalid", folder)
                install_credential("sender_email", "rotated@example.invalid", folder)
            self.assertEqual(Path(folder, "sender_email").read_text(encoding="utf-8"), "rotated@example.invalid")
            self.assertNotIn("sender@example.invalid", output.getvalue())
            self.assertNotIn("rotated@example.invalid", output.getvalue())
            with self.assertRaises(ValueError):
                install_credential("../wrong", "secret", folder)

    def test_send_status_does_not_print_email_addresses(self):
        from app import cli

        today = beijing_now().strftime("%Y-%m-%d")
        values = {"MAIL_FROM": "sender@example.invalid", "SMTP_USERNAME": "sender@example.invalid", "MAIL_TO": "recipient@example.invalid", "SMTP_AUTH_CODE": "fake-auth"}
        with tempfile.TemporaryDirectory(dir=".") as directory:
            html = Path(directory, "preview.html")
            plain = Path(directory, "preview.txt")
            html.write_text("<p>preview</p>", encoding="utf-8")
            plain.write_text("preview", encoding="utf-8")
            row = {"status": "preview", "digest_date": today, "subject": f"{today} 世界新闻", "html_path": str(html), "text_path": str(plain), "idempotency_key": recipient_binding(today, values["MAIL_TO"], values["SMTP_AUTH_CODE"])}
            calls = []
            class FakeDatabase:
                def __init__(self):
                    self.conn = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: row))
                def claim_send(self, digest_id):
                    calls.append("claimed")
                def finish_send(self, digest_id, status):
                    calls.append(status)
                def close(self):
                    pass
            class FakeMailer:
                def __init__(self, credentials):
                    pass
                def resolve_auth_code(self):
                    return values["SMTP_AUTH_CODE"]
                def send(self, *args, **kwargs):
                    calls.append("sent")
            output = io.StringIO()
            with patch("app.cli.CredentialProvider", return_value=StaticCredentials(values)), patch("app.cli.Database", FakeDatabase), patch("app.cli.SMTPMailer", FakeMailer), contextlib.redirect_stdout(output):
                cli.send_digest(f"full-{today}")
            self.assertEqual(calls, ["claimed", "sent", "sent"])
            self.assertEqual(json.loads(output.getvalue())["status"], "sent")
            self.assertNotIn(values["MAIL_FROM"], output.getvalue())
            self.assertNotIn(values["MAIL_TO"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
