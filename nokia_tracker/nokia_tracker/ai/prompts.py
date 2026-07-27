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
