import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.llm import ModelClient
from app.pipeline import _model_profile, _response_with_complete_prefix, _salvage_valid_drafts


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

    def test_length_response_salvages_complete_prefix_only(self):
        first={"event_id":"event-1","claims":[]}
        raw='{"events":['+json.dumps(first)+',{"event_id":"unfinished"'
        response={"choices":[{"finish_reason":"length","message":{"content":raw}}]}
        recovered,truncated=_response_with_complete_prefix(response)
        self.assertTrue(truncated)
        self.assertEqual(json.loads(recovered["choices"][0]["message"]["content"])["events"],[first])
        self.assertEqual(response["choices"][0]["message"]["content"],raw)
        with self.assertRaises(RuntimeError):
            _response_with_complete_prefix({"choices":[{"finish_reason":"length","message":{"content":'{"events":[{"event_id":'}}]})

    def test_runaway_event_list_is_bounded_before_validation(self):
        raw=json.dumps({"events":[{"event_id":str(index)} for index in range(200)]})
        valid,rejected=_salvage_valid_drafts({"choices":[{"message":{"content":raw}}]},[],30)
        self.assertEqual(len(valid),0)
        self.assertEqual(len(rejected),90)

    def test_cli_auto_send_only_after_complete_generation(self):
        from app import cli
        with patch.object(sys,"argv",["newsdaily","full-run","--send"]), patch("app.cli.load_sources",return_value=[]), patch("app.cli.run_full",return_value={"digest_id":"full-2099-01-01","sent":False}) as run, patch("app.cli.send_digest") as send:
            cli.main()
        self.assertTrue(run.call_args.kwargs["send"])
        send.assert_called_once_with("full-2099-01-01")
        with patch.object(sys,"argv",["newsdaily","full-run","--send"]), patch("app.cli.load_sources",return_value=[]), patch("app.cli.run_full",side_effect=RuntimeError("invalid")), patch("app.cli.send_digest") as send:
            with self.assertRaises(RuntimeError): cli.main()
            send.assert_not_called()


if __name__=="__main__": unittest.main()
