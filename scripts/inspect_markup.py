"""Read-only snippets from an allowed public page for adapter development."""
from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.collector import UA, robots_allowed


def main():
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("needles",nargs="+")
    args=parser.parse_args()
    parsed=urlparse(args.url)
    origin=f"{parsed.scheme}://{parsed.netloc}/"
    if not robots_allowed(origin,args.url): raise SystemExit("robots blocked or unavailable")
    with urlopen(Request(args.url,headers={"User-Agent":UA}),timeout=10) as response:
        raw=response.read(1_000_000)
        print("final_url=",response.geturl(),"content_type=",response.headers.get("Content-Type"))
    text=raw.decode("utf-8",errors="replace")
    for needle in args.needles:
        matches=list(re.finditer(re.escape(needle),text,re.I))[:3]
        print("needle=",needle,"matches=",len(matches))
        for match in matches:
            print(re.sub(r"\s+"," ",text[max(0,match.start()-170):match.end()+220]))


if __name__=="__main__": main()
