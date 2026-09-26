from __future__ import annotations
from dataclasses import dataclass

@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]

def validate_event(event: dict, article_ids: set[str]) -> ValidationResult:
    errors=[]
    if not event.get("event_id"): errors.append("missing event_id")
    if not event.get("summary"): errors.append("missing summary")
    if not event.get("title"): errors.append("missing title")
    if not event.get("uncertainty"): errors.append("missing uncertainty statement")
    if not event.get("claims"): errors.append("event has no claims")
    for claim in event.get("claims", []):
        if not claim.get("text"): errors.append("claim text is empty")
        if not claim.get("attribution"): errors.append("claim attribution is empty")
        if claim.get("status") not in {"supported","single_source","allegation"}: errors.append("claim status is invalid")
        refs = claim.get("supporting_article_ids", [])
        if not refs: errors.append(f"claim has no supporting article: {claim.get('text','')[:40]}")
        bad = set(refs) - article_ids
        if bad: errors.append(f"invalid article ids: {sorted(bad)}")
        evidence=claim.get("evidence",[])
        if not evidence: errors.append("claim has no evidence snippets")
        for item in evidence:
            if item.get("article_id") not in article_ids: errors.append("evidence has invalid article id")
            if not item.get("quote"): errors.append("evidence quote is empty")
            if item.get("article_id") not in refs: errors.append("evidence article is not in claim references")
    for diff in event.get("differences", []):
        if not diff.get("article_ids"): errors.append("difference has no article ids")
        bad = set(diff.get("article_ids", [])) - article_ids
        if bad: errors.append(f"invalid difference article ids: {sorted(bad)}")
    return ValidationResult(not errors, errors)
