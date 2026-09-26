"""Inspect a small public RSS/Atom feed without treating it as article content."""
from __future__ import annotations

import argparse
import sys
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from app.collector import UA, robots_allowed


def main():
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser()
    parser.add_argument("url")
    args=parser.parse_args()
    parsed=urlparse(args.url)
    origin=f"{parsed.scheme}://{parsed.netloc}/"
    if not robots_allowed(origin,args.url): raise SystemExit("robots blocked or unavailable")
    with urlopen(Request(args.url,headers={"User-Agent":UA,"Accept":"application/rss+xml,application/atom+xml,application/xml,text/xml"}),timeout=10) as response:
        raw=response.read(1_000_000)
        print("final_url=",response.geturl(),"content_type=",response.headers.get("Content-Type"))
    root=ET.fromstring(raw)
    print("root=",root.tag)
    for item in root.findall(".//item")[:4]:
        print({"title":item.findtext("title"),"link":item.findtext("link"),"date":item.findtext("pubDate"),"description_chars":len(item.findtext("description") or "")})
    atom="{http://www.w3.org/2005/Atom}"
    for item in root.findall(f".//{atom}entry")[:4]:
        link=item.find(f"{atom}link")
        print({"title":item.findtext(f"{atom}title"),"link":link.get("href") if link is not None else "","date":item.findtext(f"{atom}updated") or item.findtext(f"{atom}published")})


if __name__=="__main__": main()
