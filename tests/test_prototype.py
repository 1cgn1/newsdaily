import unittest

from app.sample import sample_articles, sample_events
from app.render import render_digest

class PrototypeRenderTests(unittest.TestCase):
    def test_sample_events_render(self):
        self.assertTrue(sample_articles())
        _,(html_body,text_body)=render_digest(sample_events(),"2026-09-25","08:00","离线样例")
        self.assertIn("<html",html_body.lower())
        self.assertTrue(text_body)

    def test_unmatched_reference_does_not_block_render(self):
        event=sample_events()[0].copy()
        event["claims"]=[{"text":"bad","supporting_article_ids":["not-real"]}]
        _,(html_body,_)=render_digest([event],"2026-09-25","08:00","离线样例")
        self.assertIn("bad",html_body)
