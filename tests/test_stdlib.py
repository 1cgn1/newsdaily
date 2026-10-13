import json
import unittest
import tempfile
from pathlib import Path
from email import policy
from email.parser import BytesParser
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from app.collector import PageParser, discover_links, normalize_url, deduplicate_articles, within_window, extract_article
from app.render import _safe_url, render_digest
from app.pipeline import _present_model_response, _check_token_limit, _model_profile, _select_diverse_articles, estimate_tokens, _rough_cluster_articles, _model_payload
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
        self.assertIn("目标选择15至30",requirements)
        self.assertIn("独立媒体交叉报道",requirements)
        self.assertNotIn("材料不足时宁可少选",requirements)

    def test_every_malformed_event_is_displayed_without_item_checks(self):
        article={"id":"art-long-identifier-123","source_name":"示例媒体","language":"zh","original_title":"原标题","url":"https://news.test/story","content_level":"full"}
        bad={"event_id":"duplicate","lead_article_id":"art-long","title":"问题条目","summary":"art- 中文对照","claims":[{"text":"引文不在正文中","supporting_article_ids":["unknown"],"status":"supported"}],"differences":[{"text":"未证实差异","article_ids":["unknown"]}]}
        response={"choices":[{"message":{"content":json.dumps({"events":[bad,bad,{},"纯文本条目"]},ensure_ascii=False)}}]}
        events,report=_present_model_response(response,[article],30)
        self.assertEqual(len(events),4)
        self.assertFalse(report["raw_fallback"])
        self.assertEqual(events[0]["sources"][0]["url"],article["url"])
        _,(html_body,text_body)=render_digest(events,"2026-10-13","08:00","测试")
        self.assertIn("问题条目",html_body)
        self.assertIn("art- 中文对照",text_body)
        self.assertIn("纯文本条目",text_body)

    def test_nonempty_unparseable_response_is_rendered_verbatim(self):
        raw="新闻原文：art- 中文对照 <未完成的 JSON"
        events,report=_present_model_response({"choices":[{"finish_reason":"length","message":{"content":raw}}]},[],30)
        self.assertTrue(report["raw_fallback"])
        _,(html_body,text_body)=render_digest(events,"2026-10-13","08:00","测试")
        self.assertIn("模型原始内容",html_body)
        self.assertIn(raw,text_body)

    def test_complete_prefix_is_rendered_even_when_tail_truncated(self):
        raw='{"events":[{"summary":"第一条"},{"summary":"第二条"},{"summary":'
        events,report=_present_model_response({"choices":[{"finish_reason":"length","message":{"content":raw}}]},[],30)
        self.assertEqual(len(events),2)
        self.assertFalse(report["raw_fallback"])
        self.assertEqual(events[1]["summary"],"第二条")









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
