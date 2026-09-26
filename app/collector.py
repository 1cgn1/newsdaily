from __future__ import annotations
import hashlib, html, json, re, socket, ssl, time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) CrossMediaDaily/0.2"
SKIP_PARTS=("/login","/subscribe","/account","/privacy","/terms","/about","/contact","/video","/live","/shows")
_robots_cache={}; _robots_lock=threading.Lock()

class _NoAutomaticRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None

_no_redirect_opener=build_opener(_NoAutomaticRedirect)

def open_same_site(url: str, headers: dict, timeout: int, check_redirect_robots: bool=False):
    """Open an HTTP(S) URL, validating each redirect before requesting it."""
    current=url
    for _ in range(4):
        parsed=urlparse(current)
        if parsed.scheme not in ("http","https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("unsafe URL for public source request")
        try:
            response=_no_redirect_opener.open(Request(current,headers=headers),timeout=timeout)
        except HTTPError as exc:
            if exc.code not in (301,302,303,307,308): raise
            location=exc.headers.get("Location")
            exc.close()
            if not location: raise ValueError("redirect missing Location")
            target=normalize_url(urljoin(current,location))
            if not _same_site(url,target): raise ValueError("redirect left source domain")
            if check_redirect_robots and not robots_decision(url,target,timeout=min(timeout,5))["allowed"]:
                raise ValueError("redirect target is blocked by robots.txt")
            current=target
            continue
        final=response.geturl()
        if not _same_site(url,final) or urlparse(final).scheme not in ("http","https"):
            response.close()
            raise ValueError("response left source domain")
        return response
    raise ValueError("too many redirects")

def robots_decision(homepage: str, target: str, timeout=5) -> dict:
    parsed=urlparse(target); origin=f"{parsed.scheme}://{parsed.netloc}"; robots_url=origin+"/robots.txt"
    with _robots_lock: cached=_robots_cache.get(origin)
    if cached is None:
        parser=RobotFileParser(robots_url)
        try:
            with open_same_site(robots_url,{"User-Agent":UA},timeout) as response: lines=response.read(128_000).decode("utf-8",errors="replace").splitlines()
            parser.parse(lines); result=(parser,"loaded",200,"")
        except HTTPError as exc:
            if exc.code in (404,410):
                parser.parse([]); result=(parser,"not_found",exc.code,"")
            else: result=(None,"unavailable",exc.code,f"HTTP {exc.code}")
        except Exception as exc:
            result=(None,"unavailable",None,f"{type(exc).__name__}: {str(exc)[:120]}")
        with _robots_lock: _robots_cache[origin]=result
        cached=result
    parser,state,http_status,error=cached
    allowed=bool(parser and state in ("loaded","not_found") and parser.can_fetch(UA,target))
    return {"allowed":allowed,"reason":"allowed" if allowed else "disallowed" if state=="loaded" else "robots_unavailable","robots_url":robots_url,"robots_http_status":http_status,"robots_error":error}

def robots_allowed(homepage: str, target: str, timeout=5):
    return robots_decision(homepage,target,timeout)["allowed"]

def classify_network_error(exc: Exception) -> str:
    if isinstance(exc,HTTPError): return f"http_{exc.code}"
    reason=exc.reason if isinstance(exc,URLError) else exc
    if isinstance(reason,ssl.SSLCertVerificationError): return "tls_certificate"
    if isinstance(reason,socket.gaierror): return "dns"
    if isinstance(reason,(TimeoutError,socket.timeout)): return "timeout"
    message=str(reason).lower()
    if "proxy" in message: return "proxy"
    if "certificate" in message or "ssl" in message: return "tls"
    if "no such file or directory" in message: return "local_network_or_proxy"
    if isinstance(exc,URLError): return "connection"
    return "parser_or_other"

def _with_limited_retry(operation, attempts=2):
    for attempt in range(attempts):
        try: return operation()
        except Exception as exc:
            retryable=classify_network_error(exc) in ("dns","timeout","connection","proxy","http_429","http_500","http_502","http_503","http_504")
            if not retryable or attempt+1>=attempts: raise
            time.sleep(0.5*(attempt+1))

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.title=[]; self.in_title=False; self.in_p=False; self.in_article=0; self.in_main=0; self.p=[]; self.paragraphs=[]; self.content_paragraphs=[]; self.links=[]; self.head_links=[]; self._href=None; self._anchor=[]; self.meta={}; self.meta_refresh=""; self.canonical=""; self.jsonld=[]; self._script_json=False; self._script=[]; self.page_language=""; self._date_capture=None; self.date_text={}
    def handle_starttag(self, tag, attrs):
        a=dict(attrs); tag=tag.lower()
        if tag=="html": self.page_language=(a.get("lang") or "").split("_")[0].lower()
        if tag=="title": self.in_title=True
        if tag=="script" and (a.get("type") or "").lower()=="application/ld+json": self._script_json=True; self._script=[]
        if tag=="article": self.in_article+=1
        if tag=="main": self.in_main+=1
        if tag=="p": self.in_p=True; self.p=[]
        if tag=="a": self._href=a.get("href"); self._anchor=[]
        if tag in ("span","div"):
            key=a.get("id") or a.get("class","")
            if key in ("pubtime_baidu","pub_time","info"):
                self._date_capture=(tag,key,[])
        if tag=="meta":
            key=(a.get("property") or a.get("name") or "").lower(); value=a.get("content","")
            if key and value: self.meta[key]=html.unescape(value.strip())
            if (a.get("http-equiv") or "").lower()=="refresh": self.meta_refresh=value
        if tag=="link":
            self.head_links.append(a)
            if "canonical" in (a.get("rel") or "").lower(): self.canonical=a.get("href","")
    def handle_endtag(self, tag):
        tag=tag.lower()
        if tag=="title": self.in_title=False
        if tag=="script" and self._script_json:
            try: self.jsonld.append(json.loads("".join(self._script)))
            except (ValueError,TypeError): pass
            self._script_json=False
        if tag=="article" and self.in_article: self.in_article-=1
        if tag=="main" and self.in_main: self.in_main-=1
        if tag=="p":
            text=re.sub(r"\s+"," ","".join(self.p)).strip()
            if len(text)>=40:
                self.paragraphs.append(text)
                if self.in_article or self.in_main: self.content_paragraphs.append(text)
            self.in_p=False
        if tag=="a" and self._href:
            text=re.sub(r"\s+"," ","".join(self._anchor)).strip()
            if text: self.links.append((self._href,text))
            self._href=None; self._anchor=[]
        if self._date_capture and tag==self._date_capture[0]:
            _,key,parts=self._date_capture; self.date_text[key]=re.sub(r"\s+"," ","".join(parts)).strip(); self._date_capture=None
    def handle_data(self, data):
        if self.in_title: self.title.append(data)
        if self.in_p: self.p.append(data)
        if self._href is not None: self._anchor.append(data)
        if self._script_json: self._script.append(data)
        if self._date_capture: self._date_capture[2].append(data)

def normalize_url(url: str) -> str:
    p=urlparse(url); path=re.sub(r"/{2,}","/",p.path or "/")
    query="&".join(x for x in p.query.split("&") if x and not x.lower().startswith(("utm_","fbclid=","gclid=","mc_cid=","mc_eid=")))
    return urlunparse((p.scheme.lower(),p.netloc.lower(),path,"",query,""))

def _jsonld_articles(nodes):
    found=[]
    def walk(x):
        if isinstance(x,list):
            for y in x: walk(y)
        elif isinstance(x,dict):
            typ=x.get("@type",[]); types=typ if isinstance(typ,list) else [typ]
            if any(str(t).lower() in ("article","newsarticle","reportagenewsarticle","analysisnewsarticle") for t in types): found.append(x)
            if "@graph" in x: walk(x["@graph"])
    for node in nodes: walk(node)
    return found

def _parse_date(value):
    if not value: return None
    try:
        normalized=str(value).replace("Z","+00:00")
        normalized=re.sub(r"([+-]\d{2})(\d{2})$",r"\1:\2",normalized)
        dt=datetime.fromisoformat(normalized)
        if dt.tzinfo is None: return None
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError: return None

def _same_site(a: str, b: str) -> bool:
    ah=urlparse(a).hostname or ""; bh=urlparse(b).hostname or ""
    return ah==bh or ah.endswith("."+bh) or bh.endswith("."+ah)

def fetch_page(url: str, timeout=15, limit=1_000_000):
    if urlparse(url).scheme not in ("http","https"): raise ValueError("unsupported URL scheme")
    with open_same_site(url,{"User-Agent":UA,"Accept":"text/html,application/xhtml+xml"},timeout,check_redirect_robots=True) as r:
        ctype=r.headers.get_content_type()
        if ctype not in ("text/html","application/xhtml+xml"): raise ValueError(f"unsupported content type {ctype}")
        raw=r.read(limit); charset=r.headers.get_content_charset(); final=r.geturl(); status=getattr(r,"status",200)
    if not charset:
        match=re.search(rb"charset\s*=\s*['\"]?([A-Za-z0-9_-]+)",raw[:10_000],re.I)
        charset=match.group(1).decode("ascii") if match else "utf-8"
    try: decoded=raw.decode(charset)
    except (UnicodeError,LookupError):
        try: decoded=raw.decode("gb18030")
        except UnicodeError: decoded=raw.decode("utf-8",errors="replace")
    parser=PageParser(); parser.charset_used=charset; parser.replacement_chars=decoded.count("\ufffd"); parser.feed(decoded); return final,status,parser

def discover_links(base_url: str, parser: PageParser, limit=4):
    scored=[]; seen=set()
    for href,text in parser.links:
        url=normalize_url(urljoin(base_url,href)); p=urlparse(url)
        if url in seen or not _same_site(base_url,url) or p.scheme not in ("http","https") or any(x in p.path.lower() for x in SKIP_PARTS): continue
        if len(text)<18 or len([x for x in p.path.split("/") if x])<2: continue
        score=min(len(text),120)+(40 if re.search(r"/20\d{2}/|20\d{2}-\d{2}",p.path) else 0)+(15 if len(p.path)>35 else 0)
        seen.add(url); scored.append((score,url,text))
    return [(u,t) for _,u,t in sorted(scored,reverse=True)[:limit]]

def source_links(source: dict, base_url: str, parser: PageParser, limit: int):
    sid=source["id"]
    if sid not in ("caixin","globaltimes","xinhuanet","mainichi","ukrinform","antara"):
        links=discover_links(base_url,parser,limit)
        if sid=="scroll": links=[(url,title) for url,title in links if not re.search(r"\b(?:Rush\s*Hour|Newswrap|daily\s+brief(?:ing)?|morning\s+roundup|week\s+in\s+review)\b",title,re.I)]
        return links[:limit]
    patterns={
        "caixin":r"/20\d{2}-\d{2}-\d{2}/\d+\.html$",
        "globaltimes":r"/page/20\d{4}/\d+\.shtml$",
        "xinhuanet":r"/world/20\d{6}/[^/]+/c\.html$",
        "mainichi":r"/english/articles/20\d{6}/[a-z0-9/]+$",
        "ukrinform":r"/rubric-[^/]+/\d+-.+\.html$",
        "antara":r"/news/\d+/.+$",
    }
    rows=[]; seen=set()
    for href,title in parser.links:
        url=normalize_url(urljoin(base_url,href)); path=urlparse(url).path
        if url in seen or not re.search(patterns[sid],path,re.I) or not any(_same_site(base,url) for base in [source["homepage"]]+source.get("section_urls",[])): continue
        if sid=="caixin" and re.search(r"早报|晚报|周报|一周|汇总|盘点|T早报",title,re.I): continue
        if len(title.strip())<12: continue
        if sid=="mainichi" and "/articles/" not in path: continue
        key=0
        if sid=="globaltimes": key=int(re.search(r"\d+(?=\.shtml$)",path).group())
        elif sid=="ukrinform": key=int(re.search(r"/(\d+)-",path).group(1))
        elif sid=="antara": key=int(re.search(r"/news/(\d+)/",path).group(1))
        elif sid=="mainichi": key=int(re.search(r"/articles/(20\d{6})/",path).group(1))
        seen.add(url)
        rows.append((key,url,title))
    if sid in ("globaltimes","ukrinform","antara","mainichi"): rows.sort(reverse=True)
    return [(url,title) for _,url,title in rows[:limit]]

def _source_date(source_id: str, parser: PageParser):
    if source_id=="caixin": value=parser.date_text.get("pubtime_baidu","")
    elif source_id=="globaltimes":
        value=parser.date_text.get("pub_time","")
        value=re.sub(r"^Published:\s*","",value,flags=re.I)
        try: return datetime.strptime(value,"%b %d, %Y %I:%M %p").replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc).isoformat()
        except ValueError: return None
    elif source_id=="xinhuanet": value=parser.date_text.get("info","")
    elif source_id=="mainichi": return _parse_date(parser.meta.get("cxenseparse:recs:publishtime"))
    else: return None
    match=re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",value)
    if not match: return None
    try: return datetime.strptime(match.group(),"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc).isoformat()
    except ValueError: return None

def extract_article(source: dict, url: str, link_text: str, timeout=8) -> dict:
    final,status,p=fetch_page(url,timeout=timeout)
    canonical=normalize_url(urljoin(final,p.canonical)) if p.canonical else normalize_url(final)
    if not any(_same_site(base,canonical) for base in [source["homepage"]]+source.get("section_urls",[])): raise ValueError("canonical URL left source domain")
    structured=_jsonld_articles(p.jsonld); article=structured[0] if structured else {}
    page_title=p.meta.get("og:title") or p.meta.get("twitter:title") or re.sub(r"\s+"," ","".join(p.title)).strip()
    title=str(article.get("headline") or page_title or link_text).strip()
    description=p.meta.get("description") or p.meta.get("og:description") or str(article.get("description") or "")
    selected_paragraphs=p.content_paragraphs or p.paragraphs
    body="\n".join(selected_paragraphs[:30]); content_level="full" if len(body)>=500 else "public_summary"
    if len(body)<120: body=description; content_level="public_summary"
    published=_source_date(source["id"],p) or _parse_date(p.meta.get("article:published_time") or p.meta.get("datepublished") or p.meta.get("date" ) or article.get("datePublished"))
    path=urlparse(canonical).path.lower()
    is_article=bool(structured or p.meta.get("article:published_time") or re.search(r"/20\d{2}/\d{1,2}/\d{1,2}/|/20\d{2}-\d{2}/",path))
    if source["id"]=="scroll" and re.search(r"\b(?:Rush\s*Hour|Newswrap|daily\s+brief(?:ing)?|morning\s+roundup|week\s+in\s+review)\b",title,re.I): is_article=False
    specific={"caixin":r"/20\d{2}-\d{2}-\d{2}/\d+\.html$","globaltimes":r"/page/20\d{4}/\d+\.shtml$","xinhuanet":r"/world/20\d{6}/[^/]+/c\.html$","mainichi":r"/english/articles/20\d{6}/[a-z0-9/]+$"}
    if source["id"] in specific: is_article=bool(re.search(specific[source["id"]],path,re.I) and published)
    if source["id"]=="caixin" and re.search(r"早报|晚报|周报|一周|汇总|盘点|T早报",title,re.I): is_article=False
    if re.search(r"\b(?:briefing|roundup|newswrap|week in review|africanews today|business africa)\b",title,re.I) or re.search(r"/(?:[^/]*-)?(?:briefing|roundup|newswrap|africanews-today|business-africa)/?$",path,re.I):
        is_article=False
    author=article.get("author",{}) if isinstance(article,dict) else {}
    if isinstance(author,list): author=author[0] if author else {}
    author_name=str(author.get("name","") if isinstance(author,dict) else author)
    provider="Associated Press" if re.search(r"Associated Press|\bAP\s+News\b|\(AP\)",author_name+" "+description+" "+body[:240],re.I) else "PR Wire" if re.search(r"PR\s*Wire|PRNewswire",author_name+" "+body,re.I) else ""
    reasons=[]
    if len(title)<12: reasons.append("short_title")
    if not is_article: reasons.append("no_article_signal")
    if len(body)<120 and len(description)<80: reasons.append("insufficient_public_text")
    if not published: reasons.append("missing_exact_publication_time")
    if provider=="PR Wire": reasons.append("press_release_not_editorial_article")
    if reasons: raise ValueError("page does not meet article-page signals: "+", ".join(reasons))
    aid="art-"+hashlib.sha256(canonical.encode()).hexdigest()[:16]
    if content_level=="public_summary" and len(body)<120: body=description or body
    clean_body=re.sub(r"\W+"," ",body).casefold().strip()
    fingerprint=hashlib.sha256(re.sub(r"\W+"," ",(title+" "+body).casefold()).encode()).hexdigest()
    syndication_key=hashlib.sha256(clean_body.encode()).hexdigest() if len(clean_body)>=250 else fingerprint
    language=p.page_language or source["language"]
    source_group="wire:associated-press" if provider=="Associated Press" else source["id"]
    if provider=="Associated Press": syndication_key="wire:associated-press:"+hashlib.sha256(re.sub(r"\W+"," ",title).casefold().strip().encode()).hexdigest()[:20]
    return {"id":aid,"source_id":source["id"],"source_name":source["name"],"title":title[:500],"original_title":title[:500],"url":canonical,"published_at":published,"language":language,"body":body[:12_000],"description":description[:1500],"content_level":content_level,"http_status":status,"fingerprint":fingerprint,"syndication_key":syndication_key,"source_group":source_group,"original_provider":provider or None,"aggregated":bool(provider)}

def article_from_restricted_feed(source: dict, item: dict) -> dict:
    """Create a traceable summary record only when terms restrict use to feed content."""
    url=normalize_url(item["url"]); title=item.get("title","").strip(); body=item.get("description","").strip(); published=item.get("published_at")
    if not _same_site(source["homepage"],url) or urlparse(url).scheme not in ("http","https"):
        raise ValueError("restricted feed item points outside source site")
    if len(title)<12 or len(body)<80 or not _parse_date(published): raise ValueError("restricted feed item lacks title, public summary, or timezone-aware date")
    aid="art-"+hashlib.sha256(url.encode()).hexdigest()[:16]
    fingerprint=hashlib.sha256((title+" "+body).casefold().encode()).hexdigest()
    return {"id":aid,"source_id":source["id"],"source_name":source["name"],"title":title[:500],"original_title":title[:500],"url":url,"published_at":_parse_date(published),"language":source["language"],"body":body[:1500],"description":body[:1500],"content_level":"restricted_feed_summary","http_status":200,"fingerprint":fingerprint,"syndication_key":fingerprint,"source_group":source["id"],"access_basis":"official_feed_content_only"}

def fetch_source(source: dict, timeout: int=8, max_articles: int=3) -> dict:
    timeout=min(20,max(3,int(source.get("timeout_seconds",timeout))))
    attempts=min(3,max(1,1+int(source.get("max_retries",1))))
    delay=max(0.8,float(source.get("request_delay_seconds",0.8)))
    url=(source.get("feed_urls") or source.get("section_urls") or [source["homepage"]])[0]
    checked=datetime.now(timezone.utc).isoformat(); articles=[]; errors=[]; diagnostics=[]; candidates=[]; article_requests=0; status=None; strategy="section"
    def result(state, final_url=url):
        dated=sum(bool(a.get("published_at")) for a in articles)
        return {"id":source["id"],"name":source["name"],"pool_status":source.get("pool_status","active"),"original_pool":source.get("original_pool",True),"status":state,"http_status":status,"url":final_url,"articles":articles,"candidate_count":len(candidates),"article_pages_requested":article_requests,"qualified_articles":len(articles),"time_parseable":dated,"within_36h":sum(within_window(a) for a in articles),"discovery_strategy_used":strategy,"article_errors":errors[:8],"diagnostics":diagnostics[:8],"example_links":[a["url"] for a in articles[:2]],"checked_at":checked}
    try:
        decision=robots_decision(source["homepage"],url,timeout=min(timeout,5))
        diagnostics.append({"phase":"discovery_robots","url":url,**decision})
        if not decision["allowed"]: return result("robots_blocked_or_unavailable")
        if source.get("feed_urls"):
            from .feeds import fetch_feed
            strategy="official_feed"
            items=_with_limited_retry(lambda: fetch_feed(url,timeout=timeout,limit=max_articles*4),attempts)
            candidates=[(x["url"],x["title"]) for x in items if _same_site(source["homepage"],x["url"])]
            final=url; status=200
            if source.get("feed_content_only"):
                strategy="official_restricted_feed_summary"
                for item in items:
                    if len(articles)>=max_articles: break
                    try: articles.append(article_from_restricted_feed(source,item))
                    except Exception as exc: errors.append({"url":item.get("url"),"class":"feed_item_rejected","error":f"{type(exc).__name__}: {str(exc)[:160]}"})
                return result("success" if articles else "no_articles",final)
        else:
            final,status,p=_with_limited_retry(lambda: fetch_page(url,timeout=timeout),attempts)
            candidates=source_links(source,final,p,limit=min(64,max_articles*int(source.get("candidate_scan_multiplier",4))))
            strategy="site_article_links" if source["id"] in ("caixin","globaltimes","xinhuanet","mainichi","ukrinform","antara") else "section"
        for article_url,title in candidates:
            if len(articles)>=max_articles: break
            decision=robots_decision(source["homepage"],article_url,timeout=min(timeout,5))
            if not decision["allowed"]:
                diagnostics.append({"phase":"article_robots","url":article_url,**decision}); continue
            article_requests+=1
            try:
                article=_with_limited_retry(lambda: extract_article(source,article_url,title,timeout=timeout),attempts)
                articles.append(article)
            except Exception as exc:
                errors.append({"url":article_url,"class":classify_network_error(exc),"error":f"{type(exc).__name__}: {str(exc)[:160]}"})
            time.sleep(delay)
        return result("success" if articles else "no_articles",final)
    except Exception as exc:
        errors.append({"url":url,"class":classify_network_error(exc),"error":f"{type(exc).__name__}: {str(exc)[:200]}"})
        return result("failed")

def collect_sources(sources: list[dict], workers: int=5, max_articles: int=3) -> list[dict]:
    out=[]; checked=datetime.now(timezone.utc).isoformat(); active=[]
    for source in sources:
        if source.get("enabled",True) and source.get("pool_status","active")=="active": active.append(source)
        else:
            pool_status=source.get("pool_status","inactive" if not source.get("enabled",True) else "candidate")
            out.append({"id":source["id"],"name":source["name"],"pool_status":pool_status,"original_pool":source.get("original_pool",True),"status":"inactive" if pool_status=="inactive" else "candidate_unverified","inactive_reason":source.get("inactive_reason") or source.get("candidate_reason") or "not enabled","url":(source.get("feed_urls") or source.get("section_urls") or [source["homepage"]])[0],"articles":[],"candidate_count":0,"article_pages_requested":0,"qualified_articles":0,"time_parseable":0,"within_36h":0,"checked_at":checked})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(fetch_source,s,8,max_articles) for s in active]
        for job in as_completed(jobs): out.append(job.result())
    return sorted(out,key=lambda x:x["id"])

def deduplicate_articles(articles):
    by_url={}; seen_fingerprints=set(); seen_titles={}; result=[]
    for article in articles:
        url=normalize_url(article["url"])
        if url in by_url: continue
        fingerprint=article.get("fingerprint") or hashlib.sha256((article.get("title","")+article.get("body","")).casefold().encode()).hexdigest()
        syndication_key=article.get("syndication_key") or hashlib.sha256(re.sub(r"\W+"," ",article.get("body","")).casefold().encode()).hexdigest()
        article=dict(article,url=url,content_hash=fingerprint,syndication_key=syndication_key)
        article["syndicated_copy"]=syndication_key in seen_titles and seen_titles[syndication_key]!=article["source_id"]
        by_url[url]=article
        if fingerprint in seen_fingerprints: article["duplicate_content"]=True
        seen_fingerprints.add(fingerprint); seen_titles.setdefault(syndication_key,article["source_id"]); result.append(article)
    return result

def within_window(article, now=None, hours=36):
    published=article.get("published_at")
    if not published: return False
    try: dt=datetime.fromisoformat(published.replace("Z","+00:00"))
    except (ValueError,AttributeError): return False
    now=now or datetime.now(timezone.utc)
    if dt.tzinfo is None: return False
    return now-timedelta(hours=hours)<=dt.astimezone(timezone.utc)<=now+timedelta(minutes=10)

def save_report(results: list[dict], path: str):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps({"checked_at":datetime.now(timezone.utc).isoformat(),"results":results},ensure_ascii=False,indent=2),encoding="utf-8")
