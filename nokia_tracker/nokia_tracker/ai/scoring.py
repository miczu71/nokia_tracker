"""Batchowe ocenianie newsów (score_news) — bierze tylko nieocenione, wołaj
łańcuch AI raz na batch, publikuje wynik do news_scores (BLUEPRINT §1, krok 6).
"""
from __future__ import annotations

import json
import logging
import sqlite3

from . import provider
from .errors import AIProviderError
from .prompts import SCORE_NEWS_SCHEMA, score_news_prompt

logger = logging.getLogger(__name__)

_TASK = "score_news"


def _unscored_news(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT n.id, n.title, n.raw_summary "
        "FROM news n LEFT JOIN news_scores s ON s.news_id = n.id "
        "WHERE s.id IS NULL ORDER BY n.published_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def score_pending(conn: sqlite3.Connection, cfg: dict) -> int:
    """Zwraca liczbę nowo ocenionych newsów. Cicho wraca 0, gdy brak
    nieocenionych newsów albo łańcuch AI zawiedzie całkowicie (wywołujący
    w main.py owija to w logger.exception, wzorem publish_sensors)."""
    batch_size = cfg["ai_news_batch_size"]
    rows = _unscored_news(conn, batch_size)
    if not rows:
        return 0

    articles = [
        {"index": i, "title": r["title"], "summary": r["raw_summary"], "source": None}
        for i, r in enumerate(rows)
    ]
    prompt = score_news_prompt(articles)

    try:
        result = provider.analyze(conn, cfg, _TASK, prompt, SCORE_NEWS_SCHEMA,
                                  cfg["ai_max_tokens"])
    except AIProviderError:
        logger.exception("score_news: łańcuch AI zawiódł, %d newsów zostaje nieocenionych",
                         len(rows))
        return 0

    model = provider.active_provider()
    scored = 0
    for item in result.get("scores", []):
        idx = item.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(rows):
            continue
        news_id = rows[idx]["id"]
        try:
            conn.execute(
                "INSERT INTO news_scores (news_id, sentiment, impact, horizon, thesis_pl, "
                "price_effect_pct_est, tags, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (news_id, item.get("sentiment"), item.get("impact"), item.get("horizon"),
                 item.get("thesis_pl"), item.get("price_effect_pct_est"),
                 json.dumps(item.get("tags") or [], ensure_ascii=False), model))
            conn.commit()
            scored += 1
        except sqlite3.IntegrityError:
            continue  # UNIQUE(news_id) — już oceniony w międzyczasie, nie nadpisujemy

    logger.info("score_news: oceniono %d/%d newsów (provider=%s)", scored, len(rows), model)
    return scored
