"""Finite, read-only collection probe for named configured media. No email or AI calls."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import getproxies

from app.collector import fetch_source


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("ids",nargs="+")
    ap.add_argument("--max-articles",type=int,default=2)
    ap.add_argument("--timeout",type=int,default=8)
    ap.add_argument("--output",default="output/source-recovery-latest.json")
    args=ap.parse_args()
    sources=json.loads(Path("config/sources.json").read_text(encoding="utf-8"))
    source_map={row["id"]:row for row in sources}
    missing=set(args.ids)-source_map.keys()
    if missing: ap.error("unknown IDs: "+", ".join(sorted(missing)))
    proxy_hosts={key:urlparse(value).hostname for key,value in getproxies().items() if key in ("http","https")}
    report={"started_at":datetime.now(timezone.utc).isoformat(),"network_exit":"local Windows host; configured proxy hostnames shown, external public IP not asserted","proxy_hosts":proxy_hosts,"max_articles_per_source":args.max_articles,"timeout_seconds":args.timeout,"results":[]}
    for sid in args.ids:
        row=fetch_source(source_map[sid],timeout=args.timeout,max_articles=args.max_articles)
        report["results"].append(row)
        print(json.dumps({key:row.get(key) for key in ("id","status","candidate_count","article_pages_requested","qualified_articles","time_parseable","within_36h","article_errors","diagnostics","example_links")},ensure_ascii=False),flush=True)
    report["finished_at"]=datetime.now(timezone.utc).isoformat()
    path=Path(args.output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Saved {path}")


if __name__=="__main__": main()
