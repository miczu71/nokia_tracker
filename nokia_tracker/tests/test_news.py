from nokia_tracker import news


# --- canonicalize_url ---

def test_canonicalize_strips_utm_params():
    a = news.canonicalize_url("https://example.com/a?utm_source=x&utm_medium=y&id=5")
    b = news.canonicalize_url("https://example.com/a?id=5")
    assert a == b == "https://example.com/a?id=5"


def test_canonicalize_strips_fragment():
    assert news.canonicalize_url("https://example.com/a#section") == "https://example.com/a"


def test_canonicalize_strips_common_tracking_params():
    assert (news.canonicalize_url("https://example.com/a?fbclid=zzz&gclid=yyy")
           == "https://example.com/a")


def test_canonicalize_keeps_non_tracking_params():
    assert (news.canonicalize_url("https://example.com/a?article=42")
           == "https://example.com/a?article=42")


# --- title_hash ---

def test_title_hash_case_and_whitespace_insensitive():
    a = news.title_hash("Nokia wins  5G   contract")
    b = news.title_hash("nokia WINS 5g CONTRACT")
    assert a == b


def test_title_hash_different_titles_differ():
    assert news.title_hash("Nokia news A") != news.title_hash("Nokia news B")


# --- seed_default_sources ---

def test_seed_default_sources_only_on_empty_table(conn):
    news.seed_default_sources(conn)
    n1 = conn.execute("SELECT COUNT(*) c FROM news_sources").fetchone()["c"]
    assert n1 == 2  # Google News RSS + GDELT

    conn.execute("DELETE FROM news_sources WHERE kind = 'rss'")
    conn.commit()
    news.seed_default_sources(conn)  # tabela NIE jest już pusta -> no-op
    n2 = conn.execute("SELECT COUNT(*) c FROM news_sources").fetchone()["c"]
    assert n2 == 1  # nie doszedł z powrotem


# --- aggregate: DEDUP (wymóg DoD kroku 5) ---

def test_aggregate_dedups_same_url_different_utm(conn, monkeypatch):
    news.seed_default_sources(conn)

    def fake_rss_fetch(c, url):
        return [
            {"title": "Nokia wins 5G contract", "url": "https://x.com/a?utm_source=twitter",
             "published_at": "2026-07-27T10:00:00+00:00", "summary": None},
            {"title": "Nokia wins 5G contract", "url": "https://x.com/a?utm_source=facebook",
             "published_at": "2026-07-27T10:00:00+00:00", "summary": None},
        ]

    monkeypatch.setattr("nokia_tracker.news.news_rss.fetch", fake_rss_fetch)
    monkeypatch.setattr("nokia_tracker.news.news_gdelt.fetch", lambda c, q: [])

    result = news.aggregate(conn)
    assert result == {"fetched": 2, "inserted": 1}  # ta sama historia, dwa utm_ -> jeden wiersz
    n = conn.execute("SELECT COUNT(*) c FROM news").fetchone()["c"]
    assert n == 1


def test_aggregate_dedups_same_title_different_source_url(conn, monkeypatch):
    # Ten sam news, RÓŻNE źródła (różny URL), identyczny tytuł+data ->
    # łapie druga warstwa dedupu: UNIQUE(title_hash, published_at).
    news.seed_default_sources(conn)

    def fake_rss_fetch(c, url):
        return [{"title": "Nokia wins 5G contract", "url": "https://source-a.com/x",
                 "published_at": "2026-07-27T10:00:00+00:00", "summary": None}]

    def fake_gdelt_fetch(c, q):
        return [{"title": "Nokia wins 5G contract", "url": "https://source-b.com/y",
                 "published_at": "2026-07-27T10:00:00+00:00", "summary": None}]

    monkeypatch.setattr("nokia_tracker.news.news_rss.fetch", fake_rss_fetch)
    monkeypatch.setattr("nokia_tracker.news.news_gdelt.fetch", fake_gdelt_fetch)

    result = news.aggregate(conn)
    assert result == {"fetched": 2, "inserted": 1}


def test_aggregate_keeps_genuinely_different_news(conn, monkeypatch):
    news.seed_default_sources(conn)

    def fake_rss_fetch(c, url):
        return [
            {"title": "Nokia wins 5G contract", "url": "https://x.com/a",
             "published_at": "2026-07-27T10:00:00+00:00", "summary": None},
            {"title": "Nokia announces Q2 results", "url": "https://x.com/b",
             "published_at": "2026-07-27T11:00:00+00:00", "summary": None},
        ]

    monkeypatch.setattr("nokia_tracker.news.news_rss.fetch", fake_rss_fetch)
    monkeypatch.setattr("nokia_tracker.news.news_gdelt.fetch", lambda c, q: [])

    result = news.aggregate(conn)
    assert result == {"fetched": 2, "inserted": 2}


def test_aggregate_running_twice_does_not_duplicate(conn, monkeypatch):
    news.seed_default_sources(conn)

    def fake_rss_fetch(c, url):
        return [{"title": "Nokia wins 5G contract", "url": "https://x.com/a",
                 "published_at": "2026-07-27T10:00:00+00:00", "summary": None}]

    monkeypatch.setattr("nokia_tracker.news.news_rss.fetch", fake_rss_fetch)
    monkeypatch.setattr("nokia_tracker.news.news_gdelt.fetch", lambda c, q: [])

    news.aggregate(conn)
    result2 = news.aggregate(conn)  # scheduler odpala to co 30 min — te same newsy wracają
    assert result2 == {"fetched": 1, "inserted": 0}
    n = conn.execute("SELECT COUNT(*) c FROM news").fetchone()["c"]
    assert n == 1


def test_aggregate_source_failure_does_not_block_others(conn, monkeypatch):
    news.seed_default_sources(conn)

    def broken_rss(c, url):
        raise ConnectionError("DNS padło")

    def fake_gdelt(c, q):
        return [{"title": "Nokia news", "url": "https://x.com/a",
                 "published_at": "2026-07-27T10:00:00+00:00", "summary": None}]

    monkeypatch.setattr("nokia_tracker.news.news_rss.fetch", broken_rss)
    monkeypatch.setattr("nokia_tracker.news.news_gdelt.fetch", fake_gdelt)

    result = news.aggregate(conn)
    assert result == {"fetched": 1, "inserted": 1}  # RSS padło, GDELT i tak przeszło


def test_aggregate_finnhub_and_marketaux_skipped_without_keys(conn, monkeypatch):
    news.seed_default_sources(conn)
    monkeypatch.setattr("nokia_tracker.news.news_rss.fetch", lambda c, url: [])
    monkeypatch.setattr("nokia_tracker.news.news_gdelt.fetch", lambda c, q: [])
    calls = []
    monkeypatch.setattr("nokia_tracker.news.news_finnhub.fetch",
                        lambda *a, **kw: calls.append(1))
    monkeypatch.setattr("nokia_tracker.news.news_marketaux.fetch",
                        lambda *a, **kw: calls.append(1))

    news.aggregate(conn, finnhub_api_key="", marketaux_api_key="")
    assert len(calls) == 0  # bez kluczy nawet nie próbujemy wołać
