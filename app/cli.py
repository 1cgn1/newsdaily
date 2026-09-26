from __future__ import annotations
import argparse, hmac, json
import sys
from datetime import datetime
from pathlib import Path
from .clock import beijing_now
from .delivery import FileMailer, SMTPMailer
from .credentials import CredentialProvider
from .privacy import recipient_binding
from .collector import collect_sources, save_report
from .llm import DeepSeekClient
from .pipeline import run_full, rebuild_preview_from_validated_response, build_reviewed_preview
from .render import render_digest, write_preview
from .sample import sample_articles, sample_events
from .storage import Database
from .validation import validate_event

ROOT=Path(__file__).resolve().parent.parent
def load_sources(): return json.loads((ROOT/"config/sources.json").read_text(encoding="utf-8"))

def seed(db):
    for s in load_sources(): db.add_source(s)
    for a in sample_articles(): db.add_article(a)
    db.conn.commit()

def digest(dry_run=True):
    db=Database(); seed(db); events=sample_events(); ids={a["id"] for a in sample_articles()}
    results=[validate_event(e,ids) for e in events]
    if not all(r.ok for r in results): raise RuntimeError(str([r.errors for r in results if not r.ok]))
    today=beijing_now().strftime("%Y-%m-%d"); subject,(h,t)=render_digest(events,today,beijing_now().strftime("%Y-%m-%d %H:%M"),"离线演示：3 篇样例文章，覆盖 Reuters、新华社、CNA；不代表真实当日新闻。")
    hp,tp=write_preview("output",today,h,t)
    did=f"demo-{today}"; db.add_digest({"id":did,"digest_date":today,"subject":subject,"html_path":str(hp),"text_path":str(tp),"status":"dry-run","created_at":datetime.now().isoformat(),"sent_at":None,"idempotency_key":f"{today}|demo|v1"})
    print(json.dumps({"subject":subject,"html":str(hp),"text":str(tp),"events":len(events),"sent":False},ensure_ascii=False,indent=2)); db.close()

def status():
    db=Database(); print(json.dumps({"database":str(db.path),"sources":db.conn.execute("select count(*) from sources").fetchone()[0],"articles":db.conn.execute("select count(*) from articles").fetchone()[0],"digests":db.conn.execute("select count(*) from digests").fetchone()[0]},ensure_ascii=False,indent=2)); db.close()

def init_db():
    db=Database()
    try:
        for source in load_sources():
            db.add_source(source)
        db.conn.commit()
        count=db.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        print(json.dumps({"database":str(db.path),"sources":count,"initialized":True},ensure_ascii=False,indent=2))
    finally:
        db.close()

def send_digest(digest_id: str):
    credentials=CredentialProvider()
    sender=credentials.get("MAIL_FROM"); recipient=credentials.get("MAIL_TO")
    if sender!=credentials.get("SMTP_USERNAME"):
        raise RuntimeError("MAIL_FROM、MAIL_TO 或 SMTP_USERNAME 未设置或发信地址不一致")
    db=Database()
    try:
        row=db.conn.execute("SELECT * FROM digests WHERE id=?",(digest_id,)).fetchone()
        if row is None or row["status"]!="preview" or not digest_id.startswith("full-"):
            raise RuntimeError("只能发送已生成、未发送的完整日报")
        expected_subject=f'{row["digest_date"]} 世界新闻'
        if row["subject"]!=expected_subject or row["digest_date"]!=beijing_now().strftime("%Y-%m-%d"):
            raise RuntimeError("邮件题目或日报日期不符合发送要求")
        mailer=SMTPMailer(credentials=credentials)
        auth_code=mailer.resolve_auth_code()
        expected_binding=recipient_binding(row["digest_date"],recipient,auth_code)
        if not hmac.compare_digest(row["idempotency_key"],expected_binding):
            raise RuntimeError("收件人与生成预览时不一致")
        html_body=Path(row["html_path"]).read_text(encoding="utf-8")
        text_body=Path(row["text_path"]).read_text(encoding="utf-8")
        if "art-" in html_body or "art-" in text_body or "中文对照" in html_body or "中文对照" in text_body:
            raise RuntimeError("预览仍包含内部文章 ID 或中英对照，已停止发信")
        db.claim_send(digest_id)
        try:
            mailer.send(sender,recipient,expected_subject,html_body,text_body,auth_code=auth_code)
        except Exception:
            db.finish_send(digest_id,"delivery_uncertain")
            raise RuntimeError("邮件发送结果不明；请人工核对收件箱与服务器，勿自动重发") from None
        db.finish_send(digest_id,"sent")
        print(json.dumps({"digest_id":digest_id,"subject":expected_subject,"status":"sent"},ensure_ascii=False))
    finally:
        db.close()

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p=argparse.ArgumentParser(description="跨媒体新闻日报本机原型"); sub=p.add_subparsers(dest="cmd",required=True)
    c=sub.add_parser("collect",help="采集来源状态"); c.add_argument("--live",action="store_true",help="真实请求各来源公开栏目"); c.add_argument("--ai-summary",action="store_true",help="对成功取得的标题/公开摘要调用 DeepSeek")
    sub.add_parser("status"); sub.add_parser("init-db",help="创建数据库并导入来源配置，不采集或发送"); f=sub.add_parser("full-run",help="真实采集、AI总结、校验并生成本地日报，不自动发信"); f.add_argument("--send",action="store_true",help="保留参数但当前仍要求人工审阅后单独发送"); f.add_argument("--reuse-collection",action="store_true",help="复用刚生成的采集报告，避免重复请求媒体"); f.add_argument("--preview-only",action="store_true",help="只生成 HTML/TXT 和报告，不创建邮件或修改已发送记录"); sub.add_parser("rebuild-preview",help="从上次通过校验的模型响应本地重建预览，不联网"); reviewed=sub.add_parser("review-preview",help="按逐字原文校正记录生成未发送预览，不联网"); reviewed.add_argument("--corrections",default="output/reviewed-evidence-corrections.json"); d=sub.add_parser("digest"); d.add_argument("--dry-run",action="store_true",default=True); s=sub.add_parser("send"); s.add_argument("--digest",required=True); s.add_argument("--confirm",action="store_true")
    a=p.parse_args()
    if a.cmd=="digest": digest(True)
    elif a.cmd=="full-run":
        result=run_full(load_sources(),send=a.send,reuse_collection=a.reuse_collection,preview_only=a.preview_only)
        if a.send:
            if not result.get("digest_id"):
                raise RuntimeError("正式生成未建立可发送日报，已停止发信")
            send_digest(result["digest_id"])
            result["sent"]=True
        print(json.dumps(result,ensure_ascii=False,indent=2))
    elif a.cmd=="status": status()
    elif a.cmd=="init-db": init_db()
    elif a.cmd=="rebuild-preview": print(json.dumps(rebuild_preview_from_validated_response(),ensure_ascii=False,indent=2))
    elif a.cmd=="review-preview": print(json.dumps(build_reviewed_preview(corrections_file=a.corrections),ensure_ascii=False,indent=2))
    elif a.cmd=="collect":
        db=Database(); sources=load_sources()
        if not a.live:
            for source in sources: db.add_source(source)
            db.conn.commit()
            print("来源池已初始化：",len(sources),"家；使用 collect --live 执行一次公开页面诊断")
        else:
            run_started=beijing_now().isoformat()
            results=collect_sources(sources); save_report(results,"output/collection-status.json")
            ok=[x for x in results if x.get("articles")]
            for s in sources: db.add_source(s)
            stored_articles=[article for result in results for article in result.get("articles",[])]
            for article in stored_articles: db.add_article(article)
            db.record_collection_run(run_started,beijing_now().isoformat(),"completed",{"sources_total":len(results),"sources_with_articles":len(ok),"articles":len(stored_articles),"failures":[{"source_id":r["id"],"status":r["status"],"error":r.get("error")} for r in results if not r.get("articles")]})
            print(json.dumps({"checked":len(results),"sources_with_articles":len(ok),"articles":sum(len(x.get('articles',[])) for x in ok),"failed_or_empty":len(results)-len(ok),"report":"output/collection-status.json"},ensure_ascii=False,indent=2))
            for x in results: print(f"{x['name']}: {x['status']}" + (f" ({x.get('http_status')})" if x.get('http_status') else f" ({x.get('error','')})"))
            if a.ai_summary and ok:
                payload={"task":"daily_digest_source_screening","rules":["只依据输入文章","不要补充未提供的事实","输出中文 JSON，字段 summary 和 selected_items","为每项保留 article_id"],"items":[{"article_id":a["id"],"source_name":a["source_name"],"title":a["title"],"text":a.get("body","")[:1000]} for x in ok for a in x["articles"]]}
                response=DeepSeekClient().complete(payload); Path("output/ai-collection-summary.json").write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding="utf-8")
                print("AI summary: output/ai-collection-summary.json")
        db.close()
    elif a.cmd=="send":
        if not a.confirm: raise SystemExit("真实发信需要显式 --confirm；先审阅 output/*.html")
        send_digest(a.digest)

if __name__=="__main__": main()
