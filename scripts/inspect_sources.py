"""Small read-only, robots-respecting inspection of configured news sources."""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse, urljoin
from urllib.request import getproxies

from app.collector import source_links, extract_article, fetch_page, robots_allowed


def inspect(source: dict, limit: int, article_samples: int) -> dict:
    section=(source.get("section_urls") or [source["homepage"]])[0]
    host=urlparse(section).hostname or ""
    result={"id":source["id"],"section":section,"proxy_schemes":sorted(getproxies()),"dns":"unavailable"}
    try:
        result["dns"]=sorted({item[4][0] for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})[:3]
    except OSError as exc:
        result["dns_error"]=f"{type(exc).__name__}: {exc}"
    if not robots_allowed(source["homepage"],section):
        result["status"]="robots_blocked_or_unavailable"
        return result
    try:
        final,status,page=fetch_page(section,timeout=8)
        result.update(status="section_ok",http_status=status,final_url=final,
                      page_title="".join(page.title)[:160],meta_refresh=page.meta_refresh,
                      charset=page.charset_used,replacement_chars=page.replacement_chars,
                      feeds=[urljoin(final,x.get("href","")) for x in page.head_links
                             if "alternate" in (x.get("rel") or "").lower()
                             and any(word in (x.get("type") or "").lower() for word in ("rss","atom","xml"))],
                      candidates=source_links(source,final,page,limit=limit))
        if article_samples:
            result["article_samples"]=[]
            for article_url,title in result["candidates"][:article_samples]:
                sample={"url":article_url}
                if not robots_allowed(source["homepage"],article_url):
                    result["article_samples"].append({"url":article_url,"status":"robots_blocked_or_unavailable"})
                    continue
                try:
                    _,_,article_page=fetch_page(article_url,timeout=8)
                    sample={"url":article_url,"page_title":"".join(article_page.title)[:160],
                            "meta":{k:v[:140] for k,v in article_page.meta.items() if any(word in k for word in ("title","date","time","description","type"))},
                            "jsonld_count":len(article_page.jsonld),"paragraph_count":len(article_page.paragraphs),
                            "first_paragraph":(article_page.content_paragraphs or article_page.paragraphs or [""])[0][:240],
                            "charset":article_page.charset_used,"replacement_chars":article_page.replacement_chars}
                    article=extract_article(source,article_url,title,timeout=8)
                    sample.update(status="accepted",title=article["title"],published_at=article["published_at"],body_length=len(article["body"]),content_level=article["content_level"])
                    result["article_samples"].append(sample)
                except Exception as exc:
                    sample.update(status="rejected",error=f"{type(exc).__name__}: {exc}")
                    result["article_samples"].append(sample)
    except Exception as exc:
        result.update(status="failed",error=f"{type(exc).__name__}: {exc}")
    return result


def main():
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser()
    parser.add_argument("ids",nargs="+")
    parser.add_argument("--links",type=int,default=8)
    parser.add_argument("--article-samples",type=int,default=0)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    sources={x["id"]:x for x in json.loads((root/"config/sources.json").read_text(encoding="utf-8"))}
    for source_id in args.ids:
        if source_id not in sources: parser.error(f"unknown source: {source_id}")
        print(json.dumps(inspect(sources[source_id],args.links,args.article_samples),ensure_ascii=False),flush=True)


if __name__=="__main__": main()
