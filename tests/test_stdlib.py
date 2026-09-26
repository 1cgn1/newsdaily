import json
import unittest
import tempfile
from pathlib import Path
from email import policy
from email.parser import BytesParser
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from app.sample import sample_articles, sample_events
from app.validation import validate_event
from app.collector import PageParser, discover_links, normalize_url, deduplicate_articles, within_window, extract_article
from app.render import _safe_url, render_digest
from app.pipeline import _parse_events, _check_token_limit, _model_profile, _select_diverse_articles, estimate_tokens, _rough_cluster_articles, _cross_source_candidates, _model_payload, _salvage_valid_drafts, _recover_events, _prioritize_valid_drafts
from app.delivery import FileMailer
from app.storage import Database
from app.clock import BEIJING_TZ

class PrototypeTests(unittest.TestCase):
    def test_beijing_date_is_independent_of_utc_vm_timezone(self):
        utc_time=datetime(2026,9,24,23,30,tzinfo=timezone.utc)
        self.assertEqual(utc_time.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M"),"2026-09-25 07:30")

    def test_digest_can_only_be_claimed_once(self):
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            path=str(Path(temporary)/"news.sqlite3")
            first=Database(path); second=Database(path)
            try:
                first.add_digest({"id":"full-2026-09-25","digest_date":"2026-09-25","subject":"2026-09-25 世界新闻","html_path":"preview.html","text_path":"preview.txt","status":"preview","created_at":"2026-09-25T00:00:00+08:00","sent_at":None,"idempotency_key":"2026-09-25|recipient|full-v2"})
                first.claim_send("full-2026-09-25")
                with self.assertRaises(RuntimeError):
                    second.claim_send("full-2026-09-25")
                self.assertEqual(second.conn.execute("SELECT status FROM digests").fetchone()[0],"sending")
            finally:
                first.close(); second.close()

    def test_all_sample_claims_have_real_sources(self):
        ids={a["id"] for a in sample_articles()}
        self.assertTrue(all(validate_event(e,ids).ok for e in sample_events()))

    def test_unknown_reference_is_rejected(self):
        event={"event_id":"x","summary":"x","claims":[{"text":"x","supporting_article_ids":["missing"]}]}
        self.assertFalse(validate_event(event,{"known"}).ok)

    def test_event_without_claims_is_rejected(self):
        self.assertFalse(validate_event({"event_id":"x","title":"x","summary":"x","claims":[]},{"a"}).ok)

    def test_discovery_keeps_same_site_articles(self):
        p=PageParser(); p.feed('<a href="/2026/09/24/a-long-story-slug">A sufficiently descriptive news headline</a><a href="https://evil.test/2026/story">External headline is long enough</a>')
        links=discover_links("https://news.test/world",p)
        self.assertEqual(links[0][0],"https://news.test/2026/09/24/a-long-story-slug")
        self.assertEqual(len(links),1)

    def test_javascript_url_is_not_rendered(self):
        self.assertEqual(_safe_url("javascript:alert(1)"),"#")

    def test_render_maps_claim_article_id_to_source(self):
        event={"event_id":"e","title":"标题","summary":"摘要","claims":[{"text":"事实","supporting_article_ids":["a1"],"attribution":"Reuters 报道","status":"single_source","evidence":[{"article_id":"a1","quote":"原文证据摘录足够长"}]}],"differences":[],"uncertainty":"尚待确认","sources":[{"article_id":"a1","name":"Reuters","language":"en","original_title":"Original headline","translated_title":"中文标题","translated_excerpt":"中文摘要对照","published_at":"2026-09-24T00:00:00Z","url":"https://news.test/a","content_level":"full"}]}
        _,(html_body,text_body)=render_digest([event],"2026-09-24","08:00","覆盖说明")
        self.assertNotIn("a1",html_body+text_body)
        self.assertNotIn("原文证据摘录足够长",html_body+text_body)
        self.assertNotIn("中文对照",html_body+text_body)
        self.assertIn("Reuters",html_body)
        self.assertIn("Original headline",html_body)
        self.assertIn("<ol><li><a",html_body)
        self.assertNotIn("<ol><li><a href=\"#e\">1.",html_body)
        self.assertEqual(FileMailer.message("a@example.com","b@example.com","2026-09-24 世界新闻",html_body,text_body)["Subject"],"2026-09-24 世界新闻")
        serialized=FileMailer.message("a@example.com","b@example.com","2026-09-24 世界新闻",html_body,text_body).as_bytes()
        self.assertEqual(BytesParser(policy=policy.default).parsebytes(serialized)["Subject"],"2026-09-24 世界新闻")

    def test_tracking_parameters_are_removed(self):
        self.assertEqual(normalize_url("https://news.test/story?utm_source=x&id=4#top"),"https://news.test/story?id=4")

    def test_missing_or_stale_publication_date_is_excluded(self):
        now=datetime(2026,9,24,tzinfo=timezone.utc)
        self.assertFalse(within_window({"published_at":None},now=now))
        self.assertFalse(within_window({"published_at":"2026-09-20T00:00:00+00:00"},now=now,hours=36))
        self.assertTrue(within_window({"published_at":"2026-09-23T12:00:00+00:00"},now=now,hours=36))

    def test_duplicate_url_and_syndication_are_marked(self):
        a={"id":"1","source_id":"a","url":"https://a.test/story?utm_source=x","title":"Same wire story","body":"same text"}
        b={"id":"2","source_id":"b","url":"https://b.test/story","title":"Same wire story","body":"same text"}
        rows=deduplicate_articles([a,a.copy(),b])
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[1]["syndicated_copy"])

    def test_article_requires_article_metadata_and_normalizes_date(self):
        parser=PageParser(); parser.feed('<html><head><title>Specific story headline</title><script type="application/ld+json">{"@type":"NewsArticle","headline":"Specific story headline","datePublished":"2026-09-24T08:00:00+08:00"}</script></head><body><p>'+('A detailed article paragraph with useful reporting facts. '*12)+'</p></body></html>')
        with patch("app.collector.fetch_page",return_value=("https://news.test/2026/story",200,parser)):
            article=extract_article({"id":"s","name":"News","homepage":"https://news.test","language":"en"},"https://news.test/2026/story","Specific story headline")
        self.assertEqual(article["published_at"],"2026-09-24T00:00:00+00:00")
        self.assertEqual(article["content_level"],"full")

    def test_claim_requires_verbatim_evidence_and_translation(self):
        article={"id":"a1","source_id":"s","source_name":"News","language":"en","title":"Article headline","original_title":"Article headline","published_at":"2026-09-24T00:00:00+00:00","content_level":"full","url":"https://news.test/2026/story","body":"Officials announced a new regional plan after a two-day meeting."}
        response={"choices":[{"message":{"content":'{"events":[{"event_id":"e1","title":"计划公布","category":"世界","summary":"官员公布区域计划。","claims":[{"text":"官员公布了计划。","supporting_article_ids":["a1"],"evidence":[{"article_id":"a1","quote":"Officials announced a new regional plan"}],"attribution":"媒体报道","status":"single_source"}],"differences":[],"uncertainty":"仍待核实","translations":[{"article_id":"a1","title_zh":"区域计划公布","excerpt_zh":"官员在两天会议后宣布新的区域计划。"}]}]}'}}]}
        events,_=_parse_events(response,[article])
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["title"],"区域计划公布")
        self.assertEqual(events[0]["title_language"],"en")
        response["choices"][0]["message"]["content"]=response["choices"][0]["message"]["content"].replace("Officials announced a new regional plan","Invented text fragment")
        with self.assertRaises(RuntimeError): _parse_events(response,[article])

    def test_two_articles_from_one_medium_are_not_independent_sources(self):
        first={"id":"a1","source_id":"bbc","source_name":"BBC News","language":"en","title":"First headline","original_title":"First headline","published_at":"2026-09-24T00:00:00+00:00","content_level":"full","url":"https://news.test/one","body":"Officials announced a new regional plan after a two-day meeting.","syndication_key":"one"}
        second={**first,"id":"a2","title":"Second headline","original_title":"Second headline","url":"https://news.test/two","syndication_key":"two"}
        event={"event_id":"e1","lead_article_id":"a1","summary":"官员公布了计划。","claims":[{"text":"官员公布了计划。","supporting_article_ids":["a1","a2"],"evidence":[{"article_id":"a1","quote":"Officials announced a new regional plan"}],"attribution":"BBC News","status":"supported"}],"differences":[],"uncertainty":"具体细节尚待确认。","translations":[{"article_id":"a1","title_zh":"第一篇中文译题"},{"article_id":"a2","title_zh":"第二篇中文译题"}]}
        response={"choices":[{"message":{"content":__import__("json").dumps({"events":[event]})}}]}
        events,_=_parse_events(response,[first,second])
        self.assertEqual(events[0]["claims"][0]["status"],"single_source")
        self.assertIn("同一家媒体",events[0]["uncertainty"])

    def test_ap_reprints_across_publishers_share_one_source_group(self):
        first={"id":"ap1","source_id":"pbs","source_name":"PBS News","source_group":"wire:associated-press","language":"en","title":"First headline","original_title":"First headline","published_at":"2026-09-24T00:00:00+00:00","content_level":"full","url":"https://pbs.test/story","body":"Officials announced a new regional plan after a two-day meeting.","syndication_key":"wire:ap:title-a"}
        second={**first,"id":"ap2","source_id":"mainichi","source_name":"The Mainichi","url":"https://mainichi.test/story","syndication_key":"wire:ap:title-b"}
        event={"event_id":"e1","lead_article_id":"ap1","summary":"Officials announced a regional plan.","claims":[{"text":"Officials announced a regional plan.","supporting_article_ids":["ap1","ap2"],"evidence":[{"article_id":"ap1","quote":"Officials announced a new regional plan"}],"attribution":"wire report","status":"supported"}],"differences":[],"uncertainty":"","translations":[{"article_id":"ap1","title_zh":"第一篇标题"},{"article_id":"ap2","title_zh":"第二篇标题"}]}
        response={"choices":[{"message":{"content":__import__("json").dumps({"events":[event]},ensure_ascii=False)}}]}
        events,_=_parse_events(response,[first,second])
        self.assertEqual(events[0]["claims"][0]["status"],"single_source")

    def test_configurable_rough_token_limit(self):
        settings={"max_input_tokens":50,"input_estimate_safety_factor":1.25}
        profile={"context_tokens":1000}
        _check_token_limit("中文测试",100,settings,profile)
        with self.assertRaisesRegex(RuntimeError,"token"):
            _check_token_limit("中"*100,100,settings,profile)
        with self.assertRaisesRegex(RuntimeError,"token"):
            _check_token_limit("中文测试",999,settings,profile)
        provider,selected=_model_profile({"model_provider":"qwen","model_profiles":{"qwen":{"max_output_tokens":16384,"context_tokens":131072}}})
        self.assertEqual(provider,"qwen")
        self.assertEqual(selected["max_output_tokens"],16384)

    def test_article_selection_round_robins_sources(self):
        articles=[{"id":f"a-{s}-{n}","source_id":s,"published_at":f"2026-09-24T0{n}:00:00+00:00"} for s in ("a","b","c") for n in range(3)]
        chosen=_select_diverse_articles(articles,6)
        self.assertEqual([x["source_id"] for x in chosen[:3]],["a","b","c"])
        self.assertEqual(len(_select_diverse_articles(articles)),9)

    def test_article_selection_round_robins_regions_before_second_source(self):
        articles=[{"id":"a1","source_id":"a","published_at":"2026-09-24T03:00:00+00:00"},{"id":"b1","source_id":"b","published_at":"2026-09-24T02:00:00+00:00"},{"id":"c1","source_id":"c","published_at":"2026-09-24T01:00:00+00:00"}]
        metadata={"a":{"region":"亚洲"},"b":{"region":"亚洲"},"c":{"region":"非洲"}}
        chosen=_select_diverse_articles(articles,3,metadata)
        self.assertEqual([x["source_id"] for x in chosen],["a","c","b"])

    def test_rough_clusters_do_not_create_source_independence(self):
        rows=[{"id":"a","source_id":"one","title":"Leaders discuss regional security agreement"},{"id":"b","source_id":"two","title":"Regional leaders discuss security agreement today"}]
        clusters=_rough_cluster_articles(rows)
        self.assertEqual(len(clusters),1)
        self.assertEqual(set(clusters[0]["source_ids"]),{"one","two"})

    def test_model_prompt_requires_fifteen_to_thirty_without_less_is_more_rule(self):
        payload=_model_payload([])
        requirements=" ".join(payload["requirements"])
        self.assertIn("15至30",requirements)
        self.assertIn("优先纳入所有",requirements)
        self.assertNotIn("材料不足时宁可少选",requirements)

    def test_event_count_is_hard_bound_for_full_run(self):
        response={"choices":[{"message":{"content":json.dumps({"events":[]})}}]}
        with self.assertRaisesRegex(RuntimeError,"below minimum 15"):
            _parse_events(response,[],event_min=15,event_max=30)
        response["choices"][0]["message"]["content"]=json.dumps({"events":[{}]*31})
        with self.assertRaisesRegex(RuntimeError,"exceeds 30"):
            _parse_events(response,[],event_min=15,event_max=30)

    def test_repeated_lead_cannot_pad_event_minimum(self):
        article={"id":"a1","source_id":"one","source_name":"媒体","language":"zh","title":"同一事件","original_title":"同一事件","url":"https://one.test/story","body":"官员公布了新的协议，双方将继续谈判。"}
        event={"event_id":"e1","lead_article_id":"a1","summary":"双方公布协议。","claims":[{"text":"双方公布协议。","supporting_article_ids":["a1"],"evidence":[{"article_id":"a1","quote":"官员公布了新的协议，双方将继续谈判。"}],"attribution":"媒体","status":"single_source"}],"differences":[],"uncertainty":"细节待确认。"}
        response={"choices":[{"message":{"content":json.dumps({"events":[event,{**event,"event_id":"e2"}]},ensure_ascii=False)}}]}
        with self.assertRaisesRegex(RuntimeError,"repeats a lead article"):
            _parse_events(response,[article])

    def test_invalid_draft_does_not_discard_individually_valid_events(self):
        articles=[{"id":aid,"source_id":source,"source_name":source,"language":"zh","title":title,"original_title":title,"url":f"https://{source}.test/story","body":"官员公布了新的协议，双方将继续谈判。"} for aid,source,title in (("a1","one","第一事件"),("a2","two","第二事件"),("a3","three","第三事件"))]
        def draft(aid,eid,quote="官员公布了新的协议，双方将继续谈判。"):
            return {"event_id":eid,"lead_article_id":aid,"summary":"双方公布协议。","claims":[{"text":"双方公布协议。","supporting_article_ids":[aid],"evidence":[{"article_id":aid,"quote":quote}],"attribution":"媒体","status":"single_source"}],"differences":[],"uncertainty":"细节待确认。"}
        response={"choices":[{"message":{"content":json.dumps({"events":[draft("a1","e1"),draft("a2","e2","编造的原文引句不在文章正文中"),draft("a3","e3")]},ensure_ascii=False)},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":50}}
        valid,rejected=_salvage_valid_drafts(response,articles,30)
        self.assertEqual([event["event_id"] for event in valid],["e1","e3"])
        self.assertEqual(len(rejected),1)
        with tempfile.TemporaryDirectory(dir=".") as directory, patch("app.pipeline._event_limits",return_value=(2,3)):
            combined,events,audit=_recover_events(response,articles,[],None,{},directory,1,[response])
            self.assertEqual(len(events),2)
            self.assertEqual(audit["repair_calls"],0)
            self.assertEqual(combined["usage"]["total_tokens"],150)

    def test_targeted_repair_restores_an_invalid_event_without_weakening_evidence(self):
        articles=[{"id":aid,"source_id":source,"source_name":source,"language":"zh","title":title,"original_title":title,"url":f"https://{source}.test/story","body":"官员公布了新的协议，双方将继续谈判。"} for aid,source,title in (("a1","one","第一事件"),("a2","two","第二事件"))]
        def draft(aid,eid,quote):
            return {"event_id":eid,"lead_article_id":aid,"summary":"双方公布协议。","claims":[{"text":"双方公布协议。","supporting_article_ids":[aid],"evidence":[{"article_id":aid,"quote":quote}],"attribution":"媒体","status":"single_source"}],"differences":[],"uncertainty":"细节待确认。"}
        original={"choices":[{"message":{"content":json.dumps({"events":[draft("a1","e1","官员公布了新的协议，双方将继续谈判。"),draft("a2","e2","原文中不存在的引句不能通过校验")]},ensure_ascii=False)},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":50}}
        repaired={"choices":[{"message":{"content":json.dumps({"events":[draft("a2","e2","官员公布了新的协议，双方将继续谈判。")]},ensure_ascii=False)},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":10}}
        class FakeClient:
            def complete(self,payload,max_tokens):
                assert max_tokens>0
                assert payload["draft_event"]["event_id"]=="e2"
                return repaired
        settings={"model_provider":"deepseek","model_profiles":{"deepseek":{"max_output_tokens":16000,"context_tokens":1000000}},"max_input_tokens":240000}
        with tempfile.TemporaryDirectory(dir=".") as directory, patch("app.pipeline._event_limits",return_value=(2,3)), patch("app.pipeline._persist_usage"):
            combined,events,audit=_recover_events(original,articles,[],FakeClient(),settings,directory,1,[original])
            self.assertEqual(len(events),2)
            self.assertEqual(audit["repair_calls"],1)
            self.assertEqual(audit["repaired_event_ids"],["e2"])
            self.assertTrue(Path(directory,"full-model-repair-1-1.json").exists())

    def test_overlong_model_list_is_salvaged_with_cross_source_priority(self):
        articles=[{"id":aid,"source_id":source,"source_name":source,"source_group":source,"language":"zh","title":title,"original_title":title,"url":f"https://{source}.test/story","body":"官员公布了新的协议，双方将继续谈判。"} for aid,source,title in (("a1","one","第一事件"),("a2","two","第二事件"),("a3","three","第三事件"))]
        def claim(ids,status):
            return {"text":"双方公布协议。","supporting_article_ids":ids,"evidence":[{"article_id":aid,"quote":"官员公布了新的协议，双方将继续谈判。"} for aid in ids],"attribution":"媒体","status":status}
        drafts=[{"event_id":"e1","lead_article_id":"a1","summary":"第一事件。","claims":[claim(["a1"],"single_source")],"differences":[],"uncertainty":"细节待确认。"},{"event_id":"e2","lead_article_id":"a2","summary":"第二事件。","claims":[claim(["a2"],"single_source")],"differences":[],"uncertainty":"细节待确认。"},{"event_id":"e3","lead_article_id":"a3","summary":"第三事件。","claims":[claim(["a2","a3"],"supported")],"differences":[],"uncertainty":"细节待确认。"}]
        response={"choices":[{"message":{"content":json.dumps({"events":drafts},ensure_ascii=False)},"finish_reason":"stop"}]}
        valid,rejected=_salvage_valid_drafts(response,articles,2)
        self.assertEqual((len(valid),len(rejected)),(3,0))
        candidate={"cluster_id":"rough-1","article_ids":["a2","a3"],"source_ids":["two","three"]}
        chosen,overflow=_prioritize_valid_drafts(valid,articles,[candidate],2)
        self.assertEqual([draft["event_id"] for draft in chosen],["e3","e1"])
        self.assertEqual(overflow,1)

    def test_cross_source_candidate_needs_independent_evidence(self):
        articles=[{"id":aid,"source_id":source,"source_name":source,"source_group":source,"language":"zh","title":"同一事件","original_title":"同一事件","url":f"https://{source}.test/story","body":"官员公布了新的协议，双方将继续谈判。"} for aid,source in (("a1","one"),("a2","two"))]
        candidates=_cross_source_candidates([{"article_id":a["id"],"source_id":a["source_id"],"source_group":a["source_group"],"rough_cluster_id":"rough-1"} for a in articles])
        self.assertEqual(len(candidates),1)
        claim={"text":"双方公布协议。","supporting_article_ids":["a1","a2"],"evidence":[{"article_id":"a1","quote":"官员公布了新的协议，双方将继续谈判。"},{"article_id":"a2","quote":"官员公布了新的协议，双方将继续谈判。"}],"attribution":"两家媒体","status":"supported"}
        event={"event_id":"e1","lead_article_id":"a1","summary":"双方公布协议。","claims":[claim],"differences":[],"uncertainty":"细节待确认。"}
        response={"choices":[{"message":{"content":json.dumps({"events":[event]},ensure_ascii=False)}}]}
        events,_=_parse_events(response,articles,required_cross_source=candidates)
        self.assertEqual(len(events),1)
        claim["evidence"].pop()
        response["choices"][0]["message"]["content"]=json.dumps({"events":[event]},ensure_ascii=False)
        with self.assertRaisesRegex(RuntimeError,"every cited article"):
            _parse_events(response,articles,required_cross_source=candidates)
        claim["supporting_article_ids"]=["a1"]
        claim["status"]="single_source"
        response["choices"][0]["message"]["content"]=json.dumps({"events":[event]},ensure_ascii=False)
        with self.assertRaisesRegex(RuntimeError,"cross-source candidate"):
            _parse_events(response,articles,required_cross_source=candidates)

    def test_token_estimate_uses_requested_character_rates(self):
        self.assertAlmostEqual(estimate_tokens("ab中文"),1.8)

    def test_non_english_source_title_is_chinese_only(self):
        event={"event_id":"es","title":"中文标题","summary":"中文摘要","claims":[],"sources":[{"name":"媒体","language":"es","original_title":"Titulo original","translated_title":"中文译题","url":"https://example.com/a"}]}
        _,(html_body,text_body)=render_digest([event],"2026-09-24","08:00","覆盖")
        self.assertIn("中文译题",html_body)
        self.assertNotIn("Titulo original",html_body+text_body)

    def test_chinese_toc_and_language_only_after_non_chinese_heading(self):
        event={"event_id":"e","title":"中文新闻标题","title_language":"en","summary":"中文摘要","claims":[],"sources":[{"name":"媒体","language":"en","original_title":"Original English headline","translated_title":"中文新闻标题","url":"https://example.com/a"}]}
        _,(html_body,text_body)=render_digest([event],"2026-09-24","08:00","覆盖")
        self.assertIn('<a href="#e">中文新闻标题</a>',html_body)
        self.assertIn('<h2>1. 中文新闻标题（英语）</h2>',html_body)
        self.assertEqual(html_body.count("（英语）"),1)
        self.assertIn("Original English headline",html_body)
        self.assertNotIn("Original English headline",text_body.split("来源：")[0])

    def test_spanish_heading_uses_spanish_label(self):
        event={"event_id":"e","title":"中文译题","title_language":"es","summary":"中文摘要","claims":[],"sources":[{"name":"媒体","language":"es","original_title":"Titulo original","translated_title":"中文译题","url":"https://example.com/a"}]}
        _,(html_body,_)=render_digest([event],"2026-09-24","08:00","覆盖")
        self.assertIn("中文译题（西班牙语）",html_body)
        self.assertNotIn("Titulo original",html_body)

    def test_storage_upserts_sources_and_articles(self):
        source={"id":"s","name":"News","homepage":"https://news.test","region":"test","language":"en","organization_type":"test","enabled":True}
        db=Database(":memory:"); db.add_source(source); db.add_source({**source,"name":"News Updated"})
        article={"id":"a","source_id":"s","title":"A specific article title","url":"https://news.test/2026/story","body":"First version","published_at":"2026-09-24T00:00:00+00:00","language":"en","content_level":"full"}
        db.add_article(article); db.add_article({**article,"body":"Updated version"})
        self.assertEqual(db.conn.execute("select count(*) from articles").fetchone()[0],1)
        self.assertEqual(db.conn.execute("select body from articles").fetchone()[0],"Updated version")
        self.assertEqual(db.conn.execute("select name from sources").fetchone()[0],"News Updated")
        db.record_collection_run("start","finish","completed",{"articles":1})
        self.assertEqual(db.conn.execute("select count(*) from collection_runs").fetchone()[0],1)
        db.close()

if __name__ == "__main__": unittest.main()
