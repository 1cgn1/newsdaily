import unittest
from unittest.mock import patch

from app.collector import PageParser, extract_article, source_links, within_window, fetch_source, classify_network_error, _with_limited_retry, collect_sources, article_from_restricted_feed, open_same_site
from urllib.error import HTTPError
from app.feeds import parse_feed


def page(markup):
    parser=PageParser(); parser.feed(markup); return parser


class SourceRecoveryFixtures(unittest.TestCase):
    def test_cross_site_redirect_is_rejected_before_second_request(self):
        redirect=HTTPError("https://news.test/story",302,"Found",{"Location":"http://127.0.0.1/private"},None)
        with patch("app.collector._no_redirect_opener.open",side_effect=redirect) as opened:
            with self.assertRaisesRegex(ValueError,"redirect left source domain"):
                open_same_site("https://news.test/story",{"User-Agent":"test"},5)
        self.assertEqual(opened.call_count,1)

    def test_redirect_to_robots_blocked_path_is_rejected_before_request(self):
        redirect=HTTPError("https://news.test/story",302,"Found",{"Location":"/blocked/story"},None)
        with patch("app.collector._no_redirect_opener.open",side_effect=redirect) as opened, patch(
            "app.collector.robots_decision",return_value={"allowed":False}
        ):
            with self.assertRaisesRegex(ValueError,"blocked by robots"):
                open_same_site("https://news.test/story",{"User-Agent":"test"},5,check_redirect_robots=True)
        self.assertEqual(opened.call_count,1)

    def test_undated_article_and_roundup_are_not_qualified(self):
        source={"id":"paper","name":"Paper","homepage":"https://news.test/","language":"en"}
        url="https://news.test/world/2026/09/25/story"
        undated=page('<title>Officials announce a regional policy update</title><main><p>'+('Officials described the policy and its regional impact. '*8)+'</p></main>')
        with patch("app.collector.fetch_page",return_value=(url,200,undated)):
            with self.assertRaisesRegex(ValueError,"missing_exact_publication_time"):
                extract_article(source,url,"Officials announce a regional policy update")
        roundup=page('<title>Ukraine war briefing: latest developments</title><meta property="article:published_time" content="2026-09-25T03:00:00+00:00"><main><p>'+('Officials described several separate developments in a daily summary. '*8)+'</p></main>')
        with patch("app.collector.fetch_page",return_value=(url,200,roundup)):
            with self.assertRaisesRegex(ValueError,"no_article_signal"):
                extract_article(source,url,"Ukraine war briefing: latest developments")

    def test_restricted_feed_summary_rejects_external_link(self):
        source={"id":"paper","name":"Paper","homepage":"https://news.test/","language":"en"}
        item={"url":"https://other.test/story","title":"A detailed article title for the feed", "description":"A public summary with reporting details and more than eighty characters about an event and its participants.", "published_at":"2026-09-25T02:00:00+00:00"}
        with self.assertRaisesRegex(ValueError,"outside source site"):
            article_from_restricted_feed(source,item)

    def test_kyiv_declared_feed_points_to_article_with_real_pubdate(self):
        home=page('<link rel="alternate" type="text/xml" href="https://kyivindependent.com/news-archive/rss/">')
        self.assertEqual(home.head_links[0]["href"],"https://kyivindependent.com/news-archive/rss/")
        feed=b'<rss><channel><item><title>Russia\'s latest target: Ukraine\'s internet</title><link>https://kyivindependent.com/russias-latest-target-ukraines-internet/</link><pubDate>Thu, 24 Sep 2026 15:59:18 GMT</pubDate></item></channel></rss>'
        item=parse_feed(feed,"https://kyivindependent.com/news-archive/rss/")[0]
        self.assertEqual(item["url"],"https://kyivindependent.com/russias-latest-target-ukraines-internet/")
        self.assertEqual(item["published_at"],"2026-09-24T15:59:18+00:00")

    def test_caixin_real_story_and_roundup_not_mixed(self):
        source={"id":"caixin","name":"财新","homepage":"https://www.caixin.com/","section_urls":["https://international.caixin.com/"],"language":"zh"}
        listing=page('<a href="https://international.caixin.com/2026-09-24/102488627.html">中美两国元首在白宫发表讲话 同忆二战峥嵘展望合作远景</a><a href="/2026-09-24/102488235.html">T早报：今日世界热点汇总与一周盘点</a>')
        links=source_links(source,"https://international.caixin.com/",listing,10)
        self.assertEqual(len(links),1)
        url=links[0][0]
        article_page=page('<html lang="zh"><title>中美两国元首在白宫发表讲话 同忆二战峥嵘展望合作远景</title><span id="pubtime_baidu">2026-09-24 23:33:13</span><main><p>'+('中美两国元首在白宫发表讲话，双方介绍了会谈安排与合作议题。'*8)+'</p></main></html>')
        with patch("app.collector.fetch_page",return_value=(url,200,article_page)):
            article=extract_article(source,url,links[0][1])
        self.assertEqual(article["published_at"],"2026-09-24T15:33:13+00:00")
        self.assertEqual(article["url"],url)
        self.assertEqual(article["content_level"],"public_summary")

    def test_global_times_page_date_not_url_month(self):
        source={"id":"globaltimes","name":"环球时报","homepage":"https://www.globaltimes.cn/","language":"en"}
        url="https://www.globaltimes.cn/page/202609/1370752.shtml"
        article_page=page('<title>Regional diplomats meet to discuss security arrangements</title><span class="pub_time">Published: Sep 17, 2026 04:46 PM</span><main><p>'+('Officials described the latest talks and their implications for regional security. '*9)+'</p></main>')
        with patch("app.collector.fetch_page",return_value=(url,200,article_page)):
            article=extract_article(source,url,"Regional diplomats meet to discuss security arrangements")
        self.assertEqual(article["published_at"],"2026-09-17T08:46:00+00:00")
        self.assertFalse(within_window(article,now=__import__("datetime").datetime(2026,9,25,tzinfo=__import__("datetime").timezone.utc)))

    def test_public_paragraphs_are_not_overwritten_by_short_meta_description(self):
        source={"id":"globaltimes","name":"环球时报","homepage":"https://www.globaltimes.cn/","language":"en"}
        url="https://www.globaltimes.cn/page/202609/1371266.shtml"
        detail="Officials reported details of a meeting and quoted participants discussing the next steps. "*4
        article_page=page('<title>Officials report new regional meeting details</title><meta name="description" content="A short teaser only"><span class="pub_time">Published: Sep 24, 2026 05:26 PM</span><main><p>'+detail+'</p></main>')
        with patch("app.collector.fetch_page",return_value=(url,200,article_page)):
            article=extract_article(source,url,"Officials report new regional meeting details")
        self.assertIn("participants discussing",article["body"])
        self.assertGreaterEqual(len(article["body"]),120)

    def test_xinhua_exact_time_and_article_path(self):
        source={"id":"xinhuanet","name":"新华社","homepage":"https://www.news.cn/","language":"zh"}
        url="https://www.news.cn/world/20260922/c418581d3ad74791856777db2fd739b3/c.html"
        article_page=page('<title>某国官员就国际合作议题举行新闻发布会</title><meta name="publishdate" content="2026-09-22"><div class="info">2026-09-22 00:48:42</div><main><p>'+('有关官员介绍国际合作的进展，并回答记者关于下一阶段工作的提问。'*8)+'</p></main>')
        with patch("app.collector.fetch_page",return_value=(url,200,article_page)):
            article=extract_article(source,url,"某国官员就国际合作议题举行新闻发布会")
        self.assertEqual(article["published_at"],"2026-09-21T16:48:42+00:00")
        self.assertEqual(article["url"],url)

    def test_nhk_shell_or_program_is_not_article(self):
        source={"id":"nhk","name":"NHK WORLD-JAPAN","homepage":"https://www3.nhk.or.jp/nhkworld/news/","language":"en"}
        shell=page('<title>Today’s Top Japan and World News</title><a href="/nhkworld/en/shows/">Watch this program</a>')
        self.assertEqual(source_links(source,source["homepage"],shell,8),[])
        with patch("app.collector.fetch_page",return_value=(source["homepage"],200,shell)):
            with self.assertRaises(ValueError): extract_article(source,source["homepage"],"Today’s Top Japan and World News")

    def test_article_robots_denial_is_not_fetched(self):
        source={"id":"dummy","name":"Dummy","homepage":"https://news.test/","section_urls":["https://news.test/world"],"language":"en"}
        section=page('<a href="/2026/09/24/news-report">A sufficiently long ordinary news report title</a>')
        def robots(_homepage,url,timeout=5):
            return {"allowed":url.endswith("/world"),"reason":"disallowed","robots_url":"https://news.test/robots.txt","robots_http_status":200,"robots_error":""}
        with patch("app.collector.robots_decision",side_effect=robots),patch("app.collector.fetch_page",return_value=(source["section_urls"][0],200,section)) as fetch:
            row=fetch_source(source,max_articles=1)
        self.assertEqual(row["status"],"no_articles")
        self.assertEqual(row["article_pages_requested"],0)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(row["diagnostics"][-1]["phase"],"article_robots")

    def test_403_is_not_retryable(self):
        self.assertEqual(classify_network_error(HTTPError("https://news.test",403,"Forbidden",{},None)),"http_403")
        calls=[]
        def rejected():
            calls.append(1)
            raise HTTPError("https://news.test",403,"Forbidden",{},None)
        with self.assertRaises(HTTPError): _with_limited_retry(rejected)
        self.assertEqual(len(calls),1)

    def test_timeout_gets_one_bounded_retry(self):
        calls=[]
        def timed_out():
            calls.append(1)
            raise TimeoutError("timed out")
        with patch("app.collector.time.sleep"):
            with self.assertRaises(TimeoutError): _with_limited_retry(timed_out)
        self.assertEqual(len(calls),2)

    def test_mainichi_article_uses_public_exact_time(self):
        source={"id":"mainichi","name":"The Mainichi","homepage":"https://mainichi.jp/english/","language":"en"}
        url="https://mainichi.jp/english/articles/20260924/p2a/00m/0na/008000c"
        markup='<title>Domestic demand helps offset visitor decline in Japan</title><meta name="cXenseParse:recs:publishtime" content="2026-09-24T10:00:00+0900"><main><p>'+('Local businesses reported changes in visitor demand and explained the regional impact. '*8)+'</p></main>'
        with patch("app.collector.fetch_page",return_value=(url,200,page(markup))):
            article=extract_article(source,url,"Domestic demand helps offset visitor decline in Japan")
        self.assertEqual(article["published_at"],"2026-09-24T01:00:00+00:00")
        self.assertEqual(article["url"],url)

    def test_ap_republication_keeps_original_provider_group(self):
        source={"id":"mainichi","name":"The Mainichi","homepage":"https://mainichi.jp/english/","language":"en"}
        url="https://mainichi.jp/english/articles/20260924/p2g/00m/0in/014000c"
        markup='<title>Diplomats receive new terminology guidance</title><meta name="description" content="NEW YORK (AP) -- Officials issued updated guidance for diplomatic communications."><meta name="cXenseParse:recs:publishtime" content="2026-09-24T10:00:00+0900"><main><p>'+('NEW YORK (AP) -- Officials issued updated guidance and described the policy context. '*8)+'</p></main>'
        with patch("app.collector.fetch_page",return_value=(url,200,page(markup))):
            article=extract_article(source,url,"Diplomats receive new terminology guidance")
        self.assertEqual(article["original_provider"],"Associated Press")
        self.assertTrue(article["aggregated"])
        self.assertTrue(article["syndication_key"].startswith("wire:associated-press:"))
        self.assertEqual(article["source_group"],"wire:associated-press")

    def test_fixed_candidate_article_samples_parse_direct_url_title_and_time(self):
        samples=[
            ("antara","ANTARA News English","https://en.antaranews.com/","https://en.antaranews.com/news/432595/ministry-bolsters-indonesias-readiness-to-become-global-halal-hub","Ministry bolsters Indonesia readiness to become global halal hub","2026-09-24T14:33:40+00:00","en"),
            ("scroll","Scroll.in","https://scroll.in/","https://scroll.in/latest/1095977/journalist-ravi-nair-gets-bail-in-adani-defamation-case","Journalist Ravi Nair gets bail in Adani defamation case","2026-09-24T12:57:00+00:00","en"),
            ("mondoweiss","Mondoweiss","https://mondoweiss.net/","https://mondoweiss.net/2026/09/palestinian-elections-gaza-west-bank-youth-launch-independent-list-in-rejection-of-factional-politics/","Palestinian Elections Gaza West Bank youth launch independent list","2026-09-24T13:05:00+00:00","en"),
            ("trtworld","TRT World","https://www.trtworld.com/","https://www.trtworld.com/article/fa2bfc47262b","Iran president says Tehran seeks deal with US not war","2026-09-25T00:17:47.090000+00:00","en"),
            ("iranintl","Iran International","https://www.iranintl.com/en","https://www.iranintl.com/en/202609242426","Iran judiciary charges six officers over teen abuse","2026-09-24T09:22:49.268000+00:00","en"),
            ("rfa","Radio Free Asia","https://www.rfa.org/english/","https://www.rfa.org/english/tibet/2026/09/24/tibet-protest-xi-jinping-china-washington-trump-photos/","Tibetan activists protest Xi Jinping Washington visit","2026-09-24T22:39:14.115000+00:00","en"),
        ]
        import json
        for sid,name,home,url,title,published,language in samples:
            with self.subTest(source=sid):
                source={"id":sid,"name":name,"homepage":home,"language":language}
                structured=json.dumps({"@type":"NewsArticle","headline":title,"datePublished":published})
                markup=f'<title>{title}</title><script type="application/ld+json">{structured}</script><main><p>'+('A detailed report describes the event, the people involved, its location, timing, and consequences. '*4)+'</p></main>'
                with patch("app.collector.fetch_page",return_value=(url,200,page(markup))):
                    article=extract_article(source,url,title)
                self.assertEqual(article["url"],url)
                self.assertEqual(article["published_at"],published.replace("Z","+00:00"))
                self.assertEqual(article["title"],title)

    def test_scroll_roundup_titles_are_not_treated_as_individual_articles(self):
        source={"id":"scroll","name":"Scroll.in","homepage":"https://scroll.in/","language":"en"}
        listing=page('<a href="/latest/1095977/journalist-ravi-nair-case">Journalist Ravi Nair gets bail in defamation case</a><a href="/latest/1095000/rush-hour">Rush Hour: Ten stories you may have missed</a>')
        links=source_links(source,"https://scroll.in/latest/",listing,10)
        self.assertEqual(len(links),1)
        url="https://scroll.in/latest/1095000/rush-hour"
        markup='<title>Rush Hour: Ten stories you may have missed</title><meta property="article:published_time" content="2026-09-24T12:00:00+00:00"><main><p>'+('A roundup of the most important stories published earlier in the day. '*5)+'</p></main>'
        with patch("app.collector.fetch_page",return_value=(url,200,page(markup))):
            with self.assertRaisesRegex(ValueError,"no_article_signal"):
                extract_article(source,url,"Rush Hour: Ten stories you may have missed")

    def test_antara_current_editorial_sample_is_not_pr_wire(self):
        source={"id":"antara","name":"ANTARA News English","homepage":"https://en.antaranews.com/","language":"en"}
        url="https://en.antaranews.com/news/432595/ministry-bolsters-indonesias-readiness-to-become-global-halal-hub"
        markup='<title>Ministry bolsters Indonesia readiness to become global halal hub</title><meta property="article:published_time" content="2026-09-24T21:33:40+07:00"><main><p>'+('The ministry described plans to strengthen the halal industry ecosystem and explained the national readiness work. '*3)+'</p></main>'
        with patch("app.collector.fetch_page",return_value=(url,200,page(markup))):
            article=extract_article(source,url,"Ministry bolsters Indonesia readiness to become global halal hub")
        self.assertEqual(article["published_at"],"2026-09-24T14:33:40+00:00")
        self.assertIsNone(article["original_provider"])
        self.assertEqual(article["content_level"],"public_summary")

    def test_antara_pr_wire_is_rejected(self):
        source={"id":"antara","name":"ANTARA","homepage":"https://en.antaranews.com/","language":"en"}
        url="https://en.antaranews.com/news/432609/company-announcement"
        markup='<title>Company announces a new commercial product for the global market</title><meta property="article:published_time" content="2026-09-24T22:58:57+07:00"><script type="application/ld+json">{"@type":"NewsArticle","headline":"Company announces a new commercial product for the global market","author":{"name":"PR Wire"}}</script><main><p>'+('Hong Kong (ANTARA/PRNewswire) company promotional statement and product claims. '*8)+'</p></main>'
        with patch("app.collector.fetch_page",return_value=(url,200,page(markup))):
            with self.assertRaisesRegex(ValueError,"press_release_not_editorial_article"):
                extract_article(source,url,"Company announces a new commercial product for the global market")

    def test_restricted_feed_summary_does_not_fetch_article_page(self):
        source={"id":"jpost","name":"The Jerusalem Post","homepage":"https://www.jpost.com/","language":"en","feed_urls":["https://www.jpost.com/rss/rssfeedsfrontpage.aspx"],"feed_content_only":True}
        item={"url":"https://www.jpost.com/israel-news/article-909619","title":"Officials announce a detailed regional policy update","published_at":"2026-09-24T10:00:00+00:00","description":"Officials announced the policy after a cabinet meeting and provided details about implementation, timing, and the agencies involved."}
        article=article_from_restricted_feed(source,item)
        self.assertEqual(article["content_level"],"restricted_feed_summary")
        self.assertEqual(article["access_basis"],"official_feed_content_only")

    def test_inactive_and_candidate_sources_remain_in_report_without_fetch(self):
        sources=[{"id":"off","name":"Off","homepage":"https://off.test/","enabled":False,"pool_status":"inactive","inactive_reason":"403"},{"id":"candidate","name":"Candidate","homepage":"https://candidate.test/","enabled":False,"pool_status":"candidate","candidate_reason":"not verified"}]
        with patch("app.collector.fetch_source") as fetch:
            rows=collect_sources(sources)
        self.assertEqual([r["status"] for r in rows],["candidate_unverified","inactive"])
        self.assertEqual(fetch.call_count,0)


if __name__=="__main__": unittest.main()
