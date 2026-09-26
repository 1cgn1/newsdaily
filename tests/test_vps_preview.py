import json
import tempfile
import unittest
from pathlib import Path

from app.pipeline import _model_input_articles


class PreviewInputTests(unittest.TestCase):
    def test_rebuild_uses_only_articles_originally_sent_to_model(self):
        report = {"results": [{"articles": [
            {"id": "art-a", "url": "https://example.invalid/a", "title": "Article A", "body": "A", "source_id": "a"},
            {"id": "art-b", "url": "https://example.invalid/b", "title": "Article B", "body": "B", "source_id": "b"},
        ]}]}
        with tempfile.TemporaryDirectory(dir=".") as directory:
            folder = Path(directory)
            (folder / "full-model-input-ids.json").write_text(json.dumps(["art-b"]), encoding="utf-8")
            self.assertEqual([a["id"] for a in _model_input_articles(folder, report)], ["art-b"])
            (folder / "full-model-input-ids.json").write_text(json.dumps(["art-missing"]), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "不一致"):
                _model_input_articles(folder, report)


if __name__ == "__main__":
    unittest.main()
