from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import urlparse

STATUS_ZH = {"supported": "多方信源验证", "single_source": "单一信源", "allegation": "尚待独立核实"}
LANGUAGE_ZH = {"en": "英语", "es": "西班牙语", "pt": "葡萄牙语", "id": "印尼语", "fr": "法语", "de": "德语", "ru": "俄语", "ar": "阿拉伯语", "ja": "日语", "ko": "韩语", "uk": "乌克兰语", "he": "希伯来语"}

def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else "#"

def _anchor(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", value)[:80] or "event"

def _source_title(source: dict) -> str:
    if source.get("language", "").split("-")[0] == "en":
        return source["original_title"]
    return source.get("translated_title") or source["original_title"]

def render_digest(events: list[dict], digest_date: str, cutoff: str, coverage: str) -> tuple[str, tuple[str, str]]:
    subject = f"{digest_date} 世界新闻"
    toc, blocks = [], []
    plain = [subject, f"统计截止：北京时间 {cutoff}", coverage, "", "目录"]
    for number, event in enumerate(events, 1):
        anchor = _anchor(event["event_id"])
        title = event["title"]
        language = event.get("title_language", "zh").split("-")[0]
        heading = title if language == "zh" else f'{title}（{LANGUAGE_ZH.get(language, "其他语种")}）'
        toc.append(f'<li><a href="#{html.escape(anchor)}">{html.escape(title)}</a></li>')
        claims = event.get("claims", [])
        claim_html = "".join(
            f'<li>{html.escape(claim["text"])} <small>（{html.escape(claim.get("attribution", "来源报道"))}；'
            f'{STATUS_ZH.get(claim.get("status"), "尚待核实")}）</small></li>'
            for claim in claims
        )
        differences = event.get("differences", [])
        difference_html = (
            "<h4>不同报道如何表述</h4><ul>"
            + "".join(f'<li>{html.escape(item["text"])}</li>' for item in differences)
            + "</ul>"
            if differences else ""
        )
        sources = event.get("sources", [])
        source_html = "".join(
            f'<li>{html.escape(source["name"])}：<a href="{html.escape(_safe_url(source["url"]), quote=True)}">'
            f'{html.escape(_source_title(source))}</a>'
            + ("（仅依据公开摘要）" if source.get("content_level") == "public_summary" else "")
            + "</li>"
            for source in sources
        )
        blocks.append(
            f'<section id="{html.escape(anchor)}"><h2>{number}. {html.escape(heading)}</h2>'
            f'<p>{html.escape(event["summary"])}</p><ul>{claim_html}</ul>{difference_html}'
            f'<p><b>目前不能确认：</b>{html.escape(event.get("uncertainty") or "无特别说明")}</p>'
            f'<h4>来源</h4><ul>{source_html}</ul></section>'
        )
        plain.extend([f"{number}. {heading}", event["summary"]])
        plain.extend(
            f'- {claim["text"]}（{claim.get("attribution", "来源报道")}；'
            f'{STATUS_ZH.get(claim.get("status"), "尚待核实")}）'
            for claim in claims
        )
        if differences:
            plain.extend(["不同报道如何表述：", *[f'- {item["text"]}' for item in differences]])
        plain.extend([f'目前不能确认：{event.get("uncertainty") or "无特别说明"}', "来源："])
        plain.extend(
            f'- {source["name"]}：{_source_title(source)} {source["url"]}'
            + ("（仅依据公开摘要）" if source.get("content_level") == "public_summary" else "")
            for source in sources
        )
        plain.append("")
    body = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;background:#f4f6f8;color:#17202a;font:16px/1.65 '
        '-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">'
        '<main style="max-width:680px;margin:auto;background:#fff;padding:24px">'
        f'<h1>世界新闻</h1><p>{html.escape(cutoff)}（北京时间）<br>{html.escape(coverage)}</p>'
        f'<h2>目录</h2><ol>{"".join(toc)}</ol>{"".join(blocks)}</main></body></html>'
    )
    return subject, (body, "\n".join(plain))

def write_preview(output_dir: str, digest_date: str, html_body: str, text_body: str):
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    html_path = path / f"digest-{digest_date}.html"
    text_path = path / f"digest-{digest_date}.txt"
    html_path.write_text(html_body, encoding="utf-8")
    text_path.write_text(text_body, encoding="utf-8")
    return html_path, text_path
