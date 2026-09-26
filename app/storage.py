from __future__ import annotations
import hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

class Database:
    def __init__(self, path: str = "data/news.sqlite3"):
        self.path = Path(path)
        if str(path) != ":memory:": self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(":memory:" if str(path)==":memory:" else self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def migrate(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, name TEXT, homepage TEXT, region TEXT, language TEXT, organization_type TEXT, enabled INTEGER, config_json TEXT);
        CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, source_id TEXT, title TEXT, original_title TEXT, url TEXT UNIQUE, published_at TEXT, language TEXT, body TEXT, content_level TEXT, content_hash TEXT, FOREIGN KEY(source_id) REFERENCES sources(id));
        CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, category TEXT, title TEXT, summary TEXT, payload_json TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS digests (id TEXT PRIMARY KEY, digest_date TEXT, subject TEXT, html_path TEXT, text_path TEXT, status TEXT, created_at TEXT, sent_at TEXT, idempotency_key TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS model_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS collection_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT, stats_json TEXT);
        """)
        columns={r[1] for r in self.conn.execute("PRAGMA table_info(articles)")}
        if "syndication_key" not in columns: self.conn.execute("ALTER TABLE articles ADD COLUMN syndication_key TEXT")
        if "duplicate_of" not in columns: self.conn.execute("ALTER TABLE articles ADD COLUMN duplicate_of TEXT")
        self.conn.commit()

    def add_source(self, source: dict):
        self.conn.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,homepage=excluded.homepage,region=excluded.region,language=excluded.language,organization_type=excluded.organization_type,enabled=excluded.enabled,config_json=excluded.config_json", (source["id"],source["name"],source["homepage"],source["region"],source["language"],source["organization_type"],int(source.get("enabled",True)),json.dumps(source,ensure_ascii=False)))

    def add_article(self, article: dict) -> str:
        aid = article.get("id") or hashlib.sha256(article["url"].encode()).hexdigest()[:16]
        body = article.get("body", "")
        self.conn.execute("INSERT INTO articles(id,source_id,title,original_title,url,published_at,language,body,content_level,content_hash,syndication_key,duplicate_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title, original_title=excluded.original_title, published_at=excluded.published_at, body=excluded.body, content_level=excluded.content_level, content_hash=excluded.content_hash, syndication_key=excluded.syndication_key, duplicate_of=excluded.duplicate_of", (aid, article["source_id"], article["title"], article.get("original_title",article["title"]), article["url"], article.get("published_at"), article.get("language"), body, article.get("content_level","full"), article.get("content_hash") or hashlib.sha256(body.encode()).hexdigest(), article.get("syndication_key"), article.get("duplicate_of")))
        self.conn.commit(); return aid

    def add_digest(self, digest: dict):
        existing=self.conn.execute("SELECT status FROM digests WHERE id=? OR idempotency_key=?",(digest["id"],digest["idempotency_key"])).fetchone()
        if existing and existing["status"] in ("sending","sent","delivery_uncertain"):
            raise RuntimeError("该日报已发送或发送状态未确认，禁止覆盖以防重复发送")
        self.conn.execute("INSERT OR REPLACE INTO digests VALUES (?,?,?,?,?,?,?,?,?)", tuple(digest[k] for k in ("id","digest_date","subject","html_path","text_path","status","created_at","sent_at","idempotency_key")))
        self.conn.commit()

    def claim_send(self, digest_id: str):
        row=self.conn.execute("SELECT * FROM digests WHERE id=?",(digest_id,)).fetchone()
        if row is None or row["status"]!="preview": raise RuntimeError("日报不存在或不处于可发送预览状态")
        update=self.conn.execute("UPDATE digests SET status='sending' WHERE id=? AND status='preview'",(digest_id,))
        if update.rowcount!=1:
            self.conn.rollback()
            raise RuntimeError("日报已被另一个发送进程认领，禁止重复发送")
        self.conn.commit()
        return dict(row)

    def finish_send(self, digest_id: str, status: str):
        if status not in ("sent","delivery_uncertain"): raise ValueError("invalid delivery status")
        self.conn.execute("UPDATE digests SET status=?, sent_at=? WHERE id=? AND status='sending'",(status,datetime.now().isoformat() if status=="sent" else None,digest_id))
        self.conn.commit()

    def close(self): self.conn.close()

    def record_collection_run(self, started_at, finished_at, status, stats):
        self.conn.execute("INSERT INTO collection_runs(started_at,finished_at,status,stats_json) VALUES(?,?,?,?)",(started_at,finished_at,status,json.dumps(stats,ensure_ascii=False)))
        self.conn.commit()
