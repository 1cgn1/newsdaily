import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.clock import BEIJING_TZ
from app.llm import ModelClient
from app.pipeline import _model_profile, _present_model_response
from app.privacy import recipient_binding
from app.render import render_digest
from app.storage import Database


class Credentials:
    def __init__(self): self.requested=[]
    def get(self,name):
        self.requested.append(name)
        return "fake-secret"


class ModelSwitchTests(unittest.TestCase):
    def test_official_providers_use_distinct_secrets_and_parameters(self):
        observed=[]
        def post(url, **kwargs):
            observed.append((url,kwargs["json"]))
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{"choices":[]})
        credentials=Credentials()
        with patch.dict(sys.modules,{"httpx":SimpleNamespace(post=post)}):
            ModelClient("deepseek",credentials=credentials).complete({},123)
            ModelClient("openai",credentials=credentials).complete({},123)
            ModelClient("qwen",base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",credentials=credentials).complete({},123)
        self.assertEqual(credentials.requested,["DEEPSEEK_API_KEY","OPENAI_API_KEY","DASHSCOPE_API_KEY"])
        self.assertEqual(observed[0][1]["thinking"],{"type":"disabled"})
        self.assertEqual(observed[1][1]["max_completion_tokens"],123)
        self.assertNotIn("thinking",observed[1][1])
        self.assertEqual(observed[2][1]["max_tokens"],123)

    def test_official_host_only_and_qwen_needs_no_pricing(self):
        with self.assertRaisesRegex(RuntimeError,"official provider host"):
            ModelClient("openai",base_url="https://example.invalid/v1",credentials=Credentials()).complete({})
        provider,_=_model_profile({"model_provider":"qwen","model_profiles":{"qwen":{"max_output_tokens":100,"context_tokens":131072}}})
        self.assertEqual(provider,"qwen")

    def test_length_response_uses_complete_prefix(self):
        first={"event_id":"event-1","claims":[]}
        raw='{"events":['+json.dumps(first)+',{"event_id":"unfinished"'
        response={"choices":[{"finish_reason":"length","message":{"content":raw}}]}
        events,report=_present_model_response(response,[],30)
        self.assertEqual(len(events),1)
        self.assertFalse(report["raw_fallback"])
        self.assertEqual(response["choices"][0]["message"]["content"],raw)
        events,report=_present_model_response({"choices":[{"finish_reason":"length","message":{"content":'{"events":[{"event_id":'}}]},[],30)
        self.assertEqual(len(events),1)
        self.assertTrue(report["raw_fallback"])

    def test_runaway_event_list_only_uses_display_cap(self):
        raw=json.dumps({"events":[{"event_id":str(index)} for index in range(200)]})
        events,report=_present_model_response({"choices":[{"message":{"content":raw}}]},[],30)
        self.assertEqual(len(events),30)
        self.assertEqual(report["excluded_by_display_cap"],170)

    def test_cli_auto_send_only_after_complete_generation(self):
        from app import cli
        with patch.object(sys,"argv",["newsdaily","full-run","--send"]), patch("app.cli.load_sources",return_value=[]), patch("app.cli.run_full",return_value={"digest_id":"full-2099-01-01","sent":False}) as run, patch("app.cli.send_digest") as send:
            cli.main()
        self.assertTrue(run.call_args.kwargs["send"])
        send.assert_called_once_with("full-2099-01-01")
        with patch.object(sys,"argv",["newsdaily","full-run","--send"]), patch("app.cli.load_sources",return_value=[]), patch("app.cli.run_full",side_effect=RuntimeError("invalid")), patch("app.cli.send_digest") as send:
            with self.assertRaises(RuntimeError): cli.main()
            send.assert_not_called()

    def test_bad_html_and_failed_item_rules_do_not_block_mocked_smtp(self):
        from app.cli import send_digest
        today="2099-01-01"
        digest_id=f"full-{today}"
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            root=Path(temporary)
            html_path=root/"preview.html"
            text_path=root/"preview.txt"
            bad_event={"event_id":"duplicate","lead_article_id":"unknown","title":"问题事件","summary":"art- 中文对照 非标准内容","claims":[{"text":"无效引文","supporting_article_ids":["unknown"],"status":"supported"}]}
            response={"choices":[{"message":{"content":json.dumps({"events":[bad_event,bad_event]},ensure_ascii=False)}}]}
            events,_=_present_model_response(response,[],30)
            _,(html_body,text_body)=render_digest(events,today,"08:00","测试")
            html_path.write_text(html_body,encoding="utf-8")
            text_path.write_text(text_body,encoding="utf-8")
            database_path=str(root/"news.sqlite3")
            db=Database(database_path)
            db.add_digest({"id":digest_id,"digest_date":today,"subject":f"{today} 世界新闻","html_path":str(html_path),"text_path":str(text_path),"status":"preview","created_at":f"{today}T08:00:00+08:00","sent_at":None,"idempotency_key":recipient_binding(today,"recipient@example.test","fake-code")})
            db.close()
            credentials=SimpleNamespace(get=lambda name:{"MAIL_FROM":"sender@example.test","SMTP_USERNAME":"sender@example.test","MAIL_TO":"recipient@example.test"}[name])
            with patch("app.cli.CredentialProvider",return_value=credentials), patch("app.cli.Database",side_effect=lambda:Database(database_path)), patch("app.cli.beijing_now",return_value=datetime(2099,1,1,8,0,tzinfo=BEIJING_TZ)), patch("app.cli.SMTPMailer") as mailer:
                mailer.return_value.resolve_auth_code.return_value="fake-code"
                send_digest(digest_id)
                mailer.return_value.send.assert_called_once()
                self.assertIn("art- 中文对照",mailer.return_value.send.call_args.args[3])
                self.assertIn("问题事件",mailer.return_value.send.call_args.args[3])
            db=Database(database_path)
            self.assertEqual(db.conn.execute("SELECT status FROM digests WHERE id=?",(digest_id,)).fetchone()[0],"sent")
            db.close()


if __name__=="__main__": unittest.main()
