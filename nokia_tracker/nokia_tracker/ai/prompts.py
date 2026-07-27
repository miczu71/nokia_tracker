"""Prompty PL + schematy JSON dla zadań AI (BLUEPRINT §1: dwa typy zadań,
nie więcej — każde kolejne to koszt). score_news tutaj; daily_analysis
dochodzi w kroku 7 razem z prognozami/rekomendacją.
"""
from __future__ import annotations

SCORE_NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer",
                              "description": "Numer artykułu z listy wejściowej, licząc od 0"},
                    "sentiment": {"type": "number",
                                 "description": "Sentyment od -1 (bardzo negatywny) do 1 (bardzo pozytywny)"},
                    "impact": {"type": "integer",
                              "description": "Waga wpływu na kurs: 0=brak, 1=niski, 2=średni, 3=wysoki"},
                    "horizon": {"type": "string",
                               "description": "immediate | weeks | quarters"},
                    "thesis_pl": {"type": "string",
                                 "description": "Jedno zdanie po polsku: dlaczego ten news ma taki wpływ"},
                    "price_effect_pct_est": {"type": "number",
                                             "description": "Szacowany wpływ na kurs w %, dodatni lub ujemny"},
                    "tags": {"type": "array", "items": {"type": "string"},
                            "description": "Z listy: 5G, patenty, kontrakt, wyniki, zarząd, makro, konkurencja"},
                },
                "required": ["index", "sentiment", "impact", "horizon", "thesis_pl", "tags"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scores"],
    "additionalProperties": False,
}


DISCLAIMER = (
    "To analiza edukacyjna generowana automatycznie, nie porada inwestycyjna. "
    "Decyzje inwestycyjne podejmujesz na własną odpowiedzialność."
)

_FORECAST_POINT_SCHEMA = {
    "type": "object",
    "properties": {
        "predicted_price": {"type": "number", "description": "Prognozowana cena w EUR"},
        "ci_low": {"type": "number", "description": "Dolna granica przedziału ufności w EUR"},
        "ci_high": {"type": "number", "description": "Górna granica przedziału ufności w EUR"},
        "confidence": {"type": "number", "description": "Pewność prognozy 0..1"},
    },
    "required": ["predicted_price", "ci_low", "ci_high", "confidence"],
    "additionalProperties": False,
}

DAILY_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "forecast_1w": _FORECAST_POINT_SCHEMA,
        "forecast_1m": _FORECAST_POINT_SCHEMA,
        "forecast_12m": _FORECAST_POINT_SCHEMA,
        "briefing_pl": {"type": "string",
                        "description": "Zwięzły briefing dzienny po polsku, maks. 600 znaków"},
        "tts_text": {"type": "string",
                    "description": "Krótsza wersja briefingu do syntezy mowy, bez symboli/skrótów"},
        "key_risks": {"type": "array", "items": {"type": "string"},
                     "description": "Lista głównych ryzyk, 1-4 pozycje"},
        "market_vs_company_verdict": {"type": "string",
                                      "description": "trend rynkowy | specyficzne dla spółki | mieszane"},
        "recommendation": {"type": "string",
                           "description": "kup | akumuluj | trzymaj | redukuj | sprzedaj"},
        "recommendation_reason_pl": {"type": "string",
                                     "description": "Uzasadnienie rekomendacji, 1-2 zdania po polsku"},
        "recommendation_confidence": {"type": "number", "description": "Pewność rekomendacji 0..1"},
    },
    "required": ["forecast_1w", "forecast_1m", "forecast_12m", "briefing_pl", "tts_text",
                "key_risks", "market_vs_company_verdict", "recommendation",
                "recommendation_reason_pl", "recommendation_confidence"],
    "additionalProperties": False,
}


def daily_analysis_prompt(context: dict) -> str:
    """context: patrz analysis.py::_build_context — statystyki serii,
    wskaźniki, względne zachowanie vs benchmarki, sentyment+top newsy,
    pozycja użytkownika (ilość + średnia cena), historyczna trafność prognoz."""
    news_lines = "\n".join(
        f"- {n['title']} (sentyment {n.get('sentiment')}, wpływ {n.get('impact')}): "
        f"{n.get('thesis_pl') or ''}"
        for n in context.get("top_news") or []
    ) or "(brak newsów z ostatnich 24h)"

    position = (
        f"{context['position_qty']} akcji, średni koszt {context['avg_cost_eur']} EUR"
        if context.get("position_qty") else "brak pozycji"
    )
    accuracy = context.get("forecast_accuracy_pct")
    accuracy_line = (f"Trafność poprzednich prognoz (MAPE-bazowana): {accuracy:.1f}%"
                     if accuracy is not None else "Brak jeszcze rozliczonych prognoz.")

    return (
        "Jesteś analitykiem rynkowym przygotowującym dzienny briefing dla inwestora "
        "indywidualnego posiadającego akcje Nokia Oyj (NOKIA.HE, Nasdaq Helsinki).\n\n"
        f"Kurs: {context['price_eur']} EUR ({context['change_pct_day']}% dzisiaj). "
        f"SMA20={context['sma_20']}, SMA50={context['sma_50']}, RSI14={context['rsi_14']}, "
        f"zmienność 30d={context['volatility_30d_pct']}%, trend={context['trend']}.\n"
        f"Względem OMXH25 (1d): {context['rel_perf_1d_vs_omxh25']} pp. "
        f"Względem Ericsson (1m): {context['rel_perf_1m_vs_ericsson']} pp. "
        f"Beta 60d={context['beta_60d']}, werdykt={context['alpha_verdict']}.\n"
        f"Sentyment newsów 24h: {context['sentiment_score']} ({context['sentiment_label']}), "
        f"{context['news_count_24h']} artykułów:\n{news_lines}\n\n"
        f"Twoja pozycja: {position}. {accuracy_line}\n\n"
        "Wygeneruj prognozy ceny na 1 tydzień, 1 miesiąc i 12 miesięcy (z przedziałem ufności "
        "i pewnością), zwięzły briefing po polsku (maks. 600 znaków) i jego wersję do TTS, "
        "listę głównych ryzyk, werdykt czy ruch jest rynkowy czy specyficzny dla spółki, oraz "
        "rekomendację (kup/akumuluj/trzymaj/redukuj/sprzedaj) z uzasadnieniem odnoszącym się "
        "do średniej ceny zakupu użytkownika, jeśli ją posiada. To analiza edukacyjna, nie "
        "porada inwestycyjna — sformułuj briefing z tą świadomością."
    )


def score_news_prompt(articles: list[dict]) -> str:
    """articles: [{'index':int,'title':str,'summary':str|None,'source':str|None}, ...]"""
    lines = [
        "Jesteś analitykiem rynkowym oceniającym wpływ newsów na kurs akcji "
        "Nokia Oyj (NOKIA.HE, Nasdaq Helsinki). Dla KAŻDEGO artykułu poniżej "
        "oceń jego sentyment, wagę wpływu na kurs, horyzont czasowy i przypisz "
        "tagi. thesis_pl to JEDNO zwięzłe zdanie po polsku uzasadniające ocenę. "
        "Zwróć wynik wyłącznie w formacie zgodnym ze schematem — jeden wpis w "
        "'scores' na każdy numer artykułu z listy, żaden nie pominięty.",
        "",
    ]
    for a in articles:
        summary = f" — {a['summary']}" if a.get("summary") else ""
        source = f" [{a['source']}]" if a.get("source") else ""
        lines.append(f"{a['index']}. {a['title']}{summary}{source}")
    return "\n".join(lines)
