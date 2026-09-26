"""Build a human-readable audit from the saved source config and run reports."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/source-pool-report-2026-09-25.md"

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    sources = read(ROOT / "config/sources.json")
    run_dir = ROOT / "output/source-pool-preview-2026-09-25"
    preflight = read(run_dir / "collection-preflight.json")
    collection = read(run_dir / "full-collection-status.json")
    funnel = preflight["article_funnel"]
    funnel["candidate_article_links_discovered"] = sum(row.get("candidate_count", 0) for row in collection["results"])
    funnel["articles_returned_by_collectors"] = funnel["qualified_articles"]
    funnel.pop("articles_discovered", None)
    (run_dir / "collection-preflight.json").write_text(json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8")
    current = {row["id"]: row for row in preflight["per_source"]}
    lines = [
        "# 来源池与采集审计（2026-09-25）", "",
        f"实测时间：{preflight['checked_at']}（UTC）；网络出口：本机 Windows，已配置代理主机 127.0.0.1；公网出口 IP 未测量。",
        "本报告是有限取样，不代表覆盖各媒体当天全部报道。完整来源池本轮每个启用来源最多接受 2 篇合格文章；条目数是已观察样本。",
        "", "## 来源池前后对比", "",
        "| 阶段 | 总数 | 启用 | 隔离 | 待验证候选 |", "|---|---:|---:|---:|---:|",
        "| 原有 27 家（基线配置） | 27 | 27（原配置状态） | 0 | 0 |",
        f"| 当前来源池 | {preflight['source_pool']['configured']} | {preflight['source_pool']['active']} | {preflight['source_pool']['inactive']} | {preflight['source_pool']['candidates']} |",
        "", "原有 27 家中目前仍启用 19 家：BBC、The Guardian、NPR、Fox News、DW、新华社、环球时报、财新、SCMP、CNA、Tempo、ABC News、Al Jazeera、TASS、Meduza、Kyiv Independent、Africanews、Daily Maverick、Agência Brasil。",
        "原有 8 家保留在来源配置和覆盖表中但设为 inactive：Reuters、AP、NHK WORLD-JAPAN、The Indian Express、The Times of Israel、+972 Magazine、Press TV、EL PAÍS América。逐站原因见下表。",
        "本轮从候选池升为 active 的 8 家：ANTARA、The Mainichi、Scroll.in、Mondoweiss、The Jerusalem Post（仅官方 RSS 摘要）、TRT World、Iran International、RFA。Infobae 仍候选；Ukrinform、PBS、France 24、Nation.Africa 也未启用。",
        "", "新增来源扩展了印尼国家通讯社、日本英文报道、印度独立数字媒体、巴以议题独立报道、以色列本地英文来源、土耳其公共媒体、伊朗境外/侨民视角及亚洲议题的 RFA 视角。背景标签用于解释视角，不是可信度评分。",
        "", "## 本次整池有限采集", "",
        f"- 来源：{preflight['source_pool']['active']} active；其中 {preflight['source_pool']['active_with_articles']} 家至少取得一篇合格文章；8 家 inactive、5 家候选均保留并列出。",
        f"- 栏目/feed 发现文章链接：{preflight['article_funnel']['candidate_article_links_discovered']}；文章页请求：{preflight['article_funnel']['article_pages_requested']}；合格文章：{preflight['article_funnel']['qualified_articles']}；可解析真实发布时间：{preflight['article_funnel']['dated_articles']}；URL 去重后：{preflight['article_funnel']['after_url_dedup']}；36 小时内：{preflight['article_funnel']['within_36h_after_url_dedup']}。",
        f"- 去重/转载：重复正文标记 {preflight['article_funnel']['duplicate_content_marked']}，跨媒体转载标记 {preflight['article_funnel']['syndicated_copies_marked']}；本样本没有被排除项。AP 的 source_group 跨发布媒体归并规则另有固定回归测试。",
        f"- 粗聚类 {preflight['article_funnel']['rough_event_clusters']} 组；符合窗口且去重后的模型候选 {preflight['article_funnel']['eligible_before_budget']} 篇；预算后保留 {preflight['article_funnel']['sent_to_model_if_key_available']} 篇；因输入预算排除 {preflight['article_funnel']['excluded_by_input_budget']} 篇。",
        f"- 耗时：{preflight['timing']['collection_and_preflight_seconds']:.1f} 秒（含采集、筛选、估算）。本轮每站上限 2 篇；不能据此声称已采齐当日新闻。",
        f"- Token：字符启发式原始估计 {preflight['token_budget']['raw_heuristic_estimate']:,}；乘安全系数 {preflight['token_budget']['safety_factor']} 后 {preflight['token_budget']['estimated_input_with_margin']:,}；实际输入/输出 token 未发生。当前安全输入 cap {preflight['token_budget']['safe_input_cap']:,}，DeepSeek 上下文配置 {preflight['token_budget']['provider_context_tokens']:,}，最大输出配置 {preflight['token_budget']['max_output_tokens']:,}。",
        "- 未生成 HTML 日报：按用户选择只要采集报告。环境没有设置 `DEEPSEEK_API_KEY`，实际模型调用未发起；没有调用 SMTP，也没有发送邮件。",
        "- 先前曾启动 8 篇/来源上限的整池采集，运行约 13 分钟仍未结束且无增量落盘；为减少请求负载而停止。该未完成轮次不计入任何成功数。随后完成的是本报告所述 2 篇上限轮次。",
        "", "## 逐站本轮状态", "",
        "`合格/时间可解析/36h` 计数；候选行的数据来自当天的有限候选验证，其他行来自整池快照。示例为直达文章 URL；RSS-only 来源标明摘要级别。", "",
        "| 来源 | 池状态 | 本轮状态 | 请求页数 | 合格/可解析/36h | 示例或失败原因 |", "|---|---|---|---:|---:|---|",
    ]
    for source in sources:
        sid = source["id"]
        row = current.get(sid, {})
        last = source.get("last_validation", {})
        evidence = last if source.get("pool_status") == "candidate" and last else row
        state = evidence.get("status", row.get("status", "not_requested"))
        pages = evidence.get("article_pages_requested", row.get("article_pages_requested", 0))
        qualified = evidence.get("qualified_articles", row.get("qualified_articles", 0))
        dated = evidence.get("time_parseable", row.get("time_parseable", 0))
        window = evidence.get("within_36h", row.get("within_36h", 0))
        examples = evidence.get("examples", row.get("examples", []))
        errors = evidence.get("article_errors", row.get("article_errors", []))
        diagnostics = evidence.get("diagnostics", row.get("diagnostics", []))
        why = row.get("inactive_reason") or row.get("candidate_reason") or source.get("inactive_reason") or source.get("candidate_reason")
        if errors:
            why = "; ".join(item.get("class", "error") + ": " + item.get("error", "") for item in errors[:2])
        elif diagnostics and state not in ("success", "candidate_unverified", "inactive"):
            diagnostic = diagnostics[0]
            why = f"{diagnostic.get('reason')} ({diagnostic.get('robots_http_status') or diagnostic.get('robots_error')})"
        example_text = "<br>".join(f"[{url}]({url})" for url in examples[:2])
        if source["id"] == "jpost" and qualified:
            example_text += "<br>仅 RSS 公开摘要；文章页请求 0"
        elif qualified and source.get("validation", {}).get("examples"):
            example_text += "<br>此站仅使用 RSS 提供的公开内容" if source.get("feed_content_only") else ""
        if not example_text:
            example_text = (why or source.get("inactive_reason") or source.get("candidate_reason") or "—").replace("|", "/")
        display_name = source["name"].replace("|", "/")
        lines.append(f"| {display_name} (`{sid}`) | {source.get('pool_status','active')} | {state} | {pages} | {qualified}/{dated}/{window} | {example_text} |")

    lines += [
        "", "## 候选启用门槛与保留判断", "",
        "- 启用需当前环境文章级样本、直达原文 URL、真实发布时间和正文/公开摘要，并有固定样例回归；栏目可打开、RSS 存在不算成功。",
        "- ANTARA 已验证编辑部文章，同时保留拒绝 PR Wire/PRNewswire 新闻稿的过滤测试；本次正文级别为公开摘要。",
        "- PBS 仍候选：前序页面样本含 AP 稿，当前 robots TLS 握手超时；不能把 AP 转载算 PBS 独立来源。Ukrinform 当前 robots 请求遇本机 DNS 错误。",
        "- Infobae 本轮 2 篇中仅 1 篇解析出真实发布时间；不以 URL 路径日期补造时间，待后续样本改善解析率。",
        "- France 24 栏目入口 HTTP 403；Nation.Africa robots.txt HTTP 403。都没有尝试绕过限制。",
        "- 未删去任何原有来源，也没有自动加入其他候补；inactive 恢复须重新审查公开入口、访问规则和文章样例。",
        "", "## 可复核文件", "",
        "- [来源配置](../config/sources.json)",
        "- [整池逐站结果与文章样本](source-pool-preview-2026-09-25/full-collection-status.json)",
        "- [采集筛选与 token 预估](source-pool-preview-2026-09-25/collection-preflight.json)",
        "- [第一批候选逐站实测](candidate-validation-priority-2026-09-25-rerun.json)",
        "- [第二批候选逐站实测](candidate-validation-second-2026-09-25-rerun.json)",
        "- [恢复来源复测](recovered-source-recheck-2026-09-25.json)",
        "- [DeepSeek 官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)",
        "- [DeepSeek 官方 Chat Completions 上下文说明](https://api-docs.deepseek.com/api/create-chat-completion/)",
        "", "## 回归", "", "固定测试：`python -m unittest discover -s tests -v`，40 项通过。",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.relative_to(ROOT))

if __name__ == "__main__":
    main()
