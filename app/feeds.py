"""Public RSS/Atom link discovery; feed text is never treated as article body."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from html import unescape
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from .collector import UA, open_same_site


def parse_feed(raw: bytes, base_url: str, limit: int = 20) -> list[dict]:
    root=ET.fromstring(raw)
    items=[]
    for node in root.findall(".//item"):
        link=(node.findtext("link") or "").strip()
        title=(node.findtext("title") or "").strip()
        published=(node.findtext("pubDate") or "").strip()
        description=unescape(re.sub(r"<[^>]+>"," ",node.findtext("description") or ""))
        description=re.sub(r"\s+"," ",description).strip()
        if link and title:
            try: date=parsedate_to_datetime(published).isoformat() if published else None
            except (TypeError,ValueError): date=None
            items.append({"url":urljoin(base_url,link),"title":title,"published_at":date,"description":description})
    atom="{http://www.w3.org/2005/Atom}"
    for node in root.findall(f".//{atom}entry"):
        links=[x for x in node.findall(f"{atom}link") if x.get("rel","alternate")=="alternate"]
        link=links[0].get("href","") if links else ""
        title=(node.findtext(f"{atom}title") or "").strip()
        date=(node.findtext(f"{atom}published") or node.findtext(f"{atom}updated") or "").strip()
        summary=node.findtext(f"{atom}summary") or node.findtext(f"{atom}content") or ""
        summary=unescape(re.sub(r"<[^>]+>"," ",summary)); summary=re.sub(r"\s+"," ",summary).strip()
        if link and title: items.append({"url":urljoin(base_url,link),"title":title,"published_at":date or None,"description":summary})
    seen=set(); out=[]
    for item in items:
        parsed=urlparse(item["url"])
        if parsed.scheme not in ("http","https") or not parsed.netloc or item["url"] in seen: continue
        seen.add(item["url"]); out.append(item)
        if len(out)>=limit: break
    return out


def fetch_feed(url: str, timeout: int = 8, limit: int = 20) -> list[dict]:
    with open_same_site(url,{"User-Agent":UA,"Accept":"application/rss+xml,application/atom+xml,application/xml,text/xml"},timeout,check_redirect_robots=True) as response:
        ctype=response.headers.get_content_type()
        if ctype not in ("application/rss+xml","application/atom+xml","application/xml","text/xml"):
            raise ValueError(f"unsupported feed content type {ctype}")
        return parse_feed(response.read(1_000_000),response.geturl(),limit)
