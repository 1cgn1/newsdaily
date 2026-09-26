from app.sample import sample_articles, sample_events
from app.validation import validate_event

def test_event_references_are_valid():
    ids={a["id"] for a in sample_articles()}
    assert all(validate_event(e,ids).ok for e in sample_events())

def test_invalid_reference_rejected():
    e=sample_events()[0].copy(); e["claims"]= [{"text":"bad","supporting_article_ids":["not-real"]}]
    assert not validate_event(e,{"art-reuters-001"}).ok
