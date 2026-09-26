"""Restore the source configuration from the durable DB snapshot and audit reports.

Used after the config file was accidentally truncated during a PowerShell 5
code-page conversion. The original 27 source definitions come from SQLite;
current inactive/candidate metadata comes from the checked-in work notes and
the finite live reports under output/.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/news.sqlite3"
OUT = ROOT / "config/sources.json"

INACTIVE = {
    "reuters": "2026-09-25 实测 robots 明确禁止当前采集入口；恢复前需合规入口和文章级复验",
    "ap": "2026-09-25 实测栏目入口 HTTP 403；官方 API 为授权服务",
    "nhk": "2026-09-25 实测入口为脚本页面，未发现可核验普通文章链接；旧 RSS 404，节目 feed 不合格",
    "indianexpress": "2026-09-25 robots.txt 返回 403，访问规则无法确认；官方 RSS 许可限个人非商业使用，待人工确认",
    "timesofisrael": "2026-09-25 robots 允许但入口 HTTP 403；不得规避站点限制",
    "972mag": "2026-09-25 入口 HTTP 403；不得规避站点限制",
    "presstv": "2026-09-25 Python 报证书验证失败，Windows Schannel 独立复核返回 CRYPT_E_REVOKED（证书已撤销）；不关闭 TLS 验证",
    "elpais": "2026-09-25 robots 允许但栏目入口 HTTP 403；不得规避站点限制",
}

CANDIDATES = [
    {"id":"antara","name":"ANTARA News English","homepage":"https://en.antaranews.com/","region":"印尼","language":"en","organization_type":"国家通讯社","funding_background":"印度尼西亚国有通讯社","perspective":"印尼及东南亚本地英文通讯社报道","discovery_strategy":"section","section_urls":["https://en.antaranews.com/world"],"notes":"优先候选；必须排除 Press Release/PR Wire"},
    {"id":"ukrinform","name":"Ukrinform","homepage":"https://www.ukrinform.net/","region":"乌克兰","language":"en","organization_type":"国家通讯社","funding_background":"乌克兰国家通讯社","perspective":"乌克兰政府与本地事件英文报道","discovery_strategy":"section","section_urls":["https://www.ukrinform.net/"],"notes":"优先候选"},
    {"id":"pbs","name":"PBS News","homepage":"https://www.pbs.org/newshour/","region":"美国","language":"en","organization_type":"公共媒体/非营利制作机构","funding_background":"News Hour Productions LLC（WETA 全资非营利子公司），含公共与披露赞助资金","perspective":"美国公共媒体国际报道与访谈","discovery_strategy":"rss","section_urls":["https://www.pbs.org/newshour/world"],"feed_urls":["https://www.pbs.org/newshour/feeds/rss/headlines"],"notes":"优先候选；须识别 AP 转载"},
    {"id":"mainichi","name":"The Mainichi","homepage":"https://mainichi.jp/english/","region":"日本","language":"en","organization_type":"商业媒体","funding_background":"每日新闻社英文数字版","perspective":"日本本地英文政治、经济与社会报道","discovery_strategy":"section","section_urls":["https://mainichi.jp/english/"],"notes":"优先候选"},
    {"id":"scroll","name":"Scroll.in","homepage":"https://scroll.in/","region":"印度","language":"en","organization_type":"独立数字媒体","funding_background":"SCSN Pvt Ltd；含会员支持","perspective":"印度国内政治、社会与区域议题","discovery_strategy":"section","section_urls":["https://scroll.in/latest/"],"notes":"优先候选；排除 Rush Hour 等每日汇总"},
    {"id":"infobae","name":"Infobae América","homepage":"https://www.infobae.com/america/","region":"拉美","language":"es","organization_type":"商业数字媒体","funding_background":"阿根廷私营数字媒体","perspective":"西语拉美多国新闻","discovery_strategy":"section","section_urls":["https://www.infobae.com/america/america-latina/"],"notes":"优先候选；核对转载署名"},
    {"id":"mondoweiss","name":"Mondoweiss","homepage":"https://mondoweiss.net/","region":"巴以/美国","language":"en","organization_type":"非营利独立媒体","funding_background":"美国非营利读者支持媒体","perspective":"巴勒斯坦、以色列及美国政策的批判性报道","discovery_strategy":"section","section_urls":["https://mondoweiss.net/"],"notes":"优先候选"},
    {"id":"jpost","name":"The Jerusalem Post","homepage":"https://www.jpost.com/","region":"以色列","language":"en","organization_type":"商业媒体","funding_background":"以色列私营英文报业","perspective":"以色列本地英文报道","discovery_strategy":"rss_restricted","section_urls":["https://www.jpost.com/"],"feed_urls":["https://www.jpost.com/rss/rssfeedsfrontpage.aspx"],"feed_content_only":True,"notes":"只使用官方 RSS 明示内容；不得抓取 feed 未提供内容"},
    {"id":"france24","name":"France 24 English","homepage":"https://www.france24.com/en/","region":"法国/全球","language":"en","organization_type":"公共国际媒体","funding_background":"France Médias Monde 法国公共国际广播集团","perspective":"法国及法语世界的国际新闻视角","discovery_strategy":"section","section_urls":["https://www.france24.com/en/"],"notes":"第二批候选"},
    {"id":"trtworld","name":"TRT World","homepage":"https://www.trtworld.com/","region":"土耳其/全球","language":"en","organization_type":"国家公共媒体","funding_background":"土耳其广播电视公司 TRT","perspective":"土耳其及周边地区英文国际报道","discovery_strategy":"section","section_urls":["https://www.trtworld.com/"],"notes":"第二批候选"},
    {"id":"iranintl","name":"Iran International","homepage":"https://www.iranintl.com/en","region":"伊朗/境外","language":"en","organization_type":"境外商业媒体","funding_background":"所有权及资金来源无法从已核对的官方公开披露中确证","perspective":"伊朗境外及侨民视角；背景标签不作可信度评分","discovery_strategy":"section","section_urls":["https://www.iranintl.com/en"],"notes":"第二批候选"},
    {"id":"rfa","name":"Radio Free Asia","homepage":"https://www.rfa.org/english/","region":"亚洲","language":"en","organization_type":"美国政府资助非营利媒体","funding_background":"美国国会拨款、USAGM 监督下的私营非营利机构","perspective":"亚洲受限媒体环境和人权议题","discovery_strategy":"section","section_urls":["https://www.rfa.org/english/"],"notes":"第二批候选"},
    {"id":"nationafrica","name":"Nation.Africa","homepage":"https://nation.africa/","region":"东非","language":"en","organization_type":"商业媒体","funding_background":"Nation Media Group","perspective":"肯尼亚、东非与非洲本地报道","discovery_strategy":"section","section_urls":["https://nation.africa/kenya/news"],"notes":"第二批候选"},
]

PROMOTED = {"antara", "mainichi", "scroll", "mondoweiss", "jpost", "trtworld", "iranintl", "rfa"}

def main():
    with sqlite3.connect(DB) as db:
        originals = [json.loads(row[0]) for row in db.execute("SELECT config_json FROM sources")]
    if len(originals) != 27 or len({s["id"] for s in originals}) != 27:
        raise RuntimeError("durable DB snapshot does not contain the expected 27 original sources")

    # Restore the known, already tested source-specific entry strategies.
    by_id = {s["id"]: s for s in originals}
    by_id["caixin"]["section_urls"] = ["https://international.caixin.com/"]
    by_id["kyivindependent"].update(discovery_strategy="rss", feed_urls=["https://kyivindependent.com/news-archive/rss/"])
    by_id["nhk"]["section_urls"] = ["https://www3.nhk.or.jp/nhkworld/news/"]

    for source in originals:
        source["original_pool"] = True
        if source["id"] in INACTIVE:
            source.update(enabled=False, pool_status="inactive", inactive_reason=INACTIVE[source["id"]])
        else:
            source.update(enabled=True, pool_status="active")

    reports = [
        ROOT / "output/candidate-validation-priority-2026-09-25-rerun.json",
        ROOT / "output/candidate-validation-second-2026-09-25-rerun.json",
    ]
    validations = {}
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for row in report["results"]:
            validations[row["id"]] = row

    for source in CANDIDATES:
        sid = source["id"]
        row = validations.get(sid)
        qualified = bool(row and row["qualified_articles"] and row["time_parseable"] and row["within_36h"])
        # Infobae had one undated page in this two-page sample; retain it until
        # a follow-up yields a stronger publication-time parse rate.
        if sid == "infobae":
            qualified = False
            source["candidate_reason"] = "本轮2篇中仅1篇可解析真实发布时间；继续有限采样，不用 URL 日期替代发布时间"
        if sid in PROMOTED and not qualified:
            raise RuntimeError(f"promotion gate failed for {sid}")
        if sid in PROMOTED:
            source.update(enabled=True, pool_status="active")
            source["validation"] = {
                "checked_at": row["checked_at"],
                "report": "output/" + ("candidate-validation-second-2026-09-25-rerun.json" if sid in {"antara", "trtworld", "iranintl", "rfa"} else "candidate-validation-priority-2026-09-25-rerun.json"),
                "article_pages_requested": row["article_pages_requested"],
                "qualified_articles": row["qualified_articles"],
                "time_parseable": row["time_parseable"],
                "within_36h": row["within_36h"],
                "examples": row["example_links"],
            }
        else:
            source.update(enabled=False, pool_status="candidate")
            reasons = {
                "ukrinform": "robots 请求本机 DNS 解析失败（Errno 2）；属于出口/DNS 未决，不判永久不可用",
                "pbs": "robots 请求 TLS 握手超时；当前网络未完成文章级验证，不判永久不可用",
                "france24": "robots 允许，但栏目页请求 HTTP 403；不绕过站点限制",
                "nationafrica": "robots.txt 返回 HTTP 403，访问规则无法确认；不继续抓取",
                "infobae": "本轮2篇中仅1篇可解析真实发布时间；继续有限采样，不用 URL 日期替代发布时间",
            }
            source["candidate_reason"] = reasons.get(sid, "本轮有限实测未完成文章级验证；保留候选并待复测")
        if row:
            source["last_validation"] = {
                "checked_at": row["checked_at"],
                "status": row["status"],
                "article_pages_requested": row["article_pages_requested"],
                "qualified_articles": row["qualified_articles"],
                "time_parseable": row["time_parseable"],
                "within_36h": row["within_36h"],
                "diagnostics": row.get("diagnostics", []),
                "article_errors": row.get("article_errors", []),
                "examples": row.get("example_links", []),
            }
        source["original_pool"] = False
        if sid == "jpost":
            source["validation"]["access_note"] = "仅官方 RSS 摘要；文章页请求数为 0"
        if sid == "antara":
            source["candidate_filter"] = "reject PR Wire/PRNewswire press releases"
        if sid == "scroll":
            source["candidate_filter"] = "exclude daily roundups such as Rush Hour"
        if sid == "pbs":
            source["candidate_filter"] = "track Associated Press original-provider attribution; no independent PBS credit for wire copies"
        originals.append(source)

    if len(originals) != 40 or len({s["id"] for s in originals}) != 40:
        raise RuntimeError("restored pool must contain 40 unique sources")
    OUT.write_text(json.dumps(originals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {state: sum(s.get("pool_status") == state for s in originals) for state in ("active", "inactive", "candidate")}
    print(json.dumps({"sources": len(originals), "original": 27, "counts": counts}, ensure_ascii=False))

if __name__ == "__main__":
    main()
