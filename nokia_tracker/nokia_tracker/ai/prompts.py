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


CHAT_INTENTS = [
    "podatek_ze_sprzedazy", "ile_moge_sprzedac", "kiedy_vesting", "ile_zarobilem",
    "dywidendy_w_roku", "koszt_sprzedazy_teraz", "porownanie_z_benchmarkiem",
    "pit_za_rok", "straty_z_lat_ubieglych", "koncentracja_majatku", "kiedy_sprzedac",
    "inne",
]

CHAT_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": "Rozpoznana intencja pytania użytkownika o własny portfel/podatki",
            "enum": CHAT_INTENTS,
        },
        # Krok 29 (naprawa 0.13.1, live): `"type": [X, "null"]` (nullable union)
        # zmierzone jako niekompatybilne z Gemini structured output — HTTP 400
        # "Proto field is not repeating, cannot start list" na DOKŁADNIE tej
        # ścieżce (properties[1] = params -> properties[0] = quantity -> type),
        # zarówno przez freellmapi (routing na Google) jak i bezpośrednio przez
        # ai/gemini.py. Żaden inny schemat w tym pliku nigdy nie używał tego
        # wzorca — wracamy do sprawdzonego: pojedynczy typ + brak w `required`
        # (ten sam wzorzec co SCORE_NEWS_SCHEMA's opcjonalne pole), model po
        # prostu POMIJA parametr, którego pytanie nie zawiera.
        "params": {
            "type": "object",
            "properties": {
                "quantity": {"type": "number",
                            "description": "Liczba akcji, jeśli pytanie jej dotyczy"},
                "year": {"type": "integer",
                        "description": "Rok podatkowy, jeśli pytanie go dotyczy"},
                "price_eur": {"type": "number",
                             "description": "Cena akcji w EUR, jeśli podana w pytaniu"},
                "horizon": {"type": "string",
                           "description": "Horyzont czasowy (np. 'rok', 'kwartał'), jeśli podany"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "confidence": {"type": "number", "description": "Pewność rozpoznania intencji, 0..1"},
    },
    "required": ["intent", "params", "confidence"],
    "additionalProperties": False,
}

CHAT_NARRATION_SCHEMA = {
    "type": "object",
    "properties": {
        "answer_pl": {"type": "string",
                     "description": "Naturalna odpowiedź po polsku, 1-3 zdania"},
    },
    "required": ["answer_pl"],
    "additionalProperties": False,
}


def chat_intent_prompt(question: str, context: dict) -> str:
    """context: {'today', 'years_with_data', 'cost_basis_policy'} — CELOWO
    bez żadnej liczby z portfela (cena, ilość akcji, wartości pieniężne):
    rozpoznanie intencji nie jest zadaniem tego wywołania, silnik Pythona
    liczy DOPIERO po rozpoznaniu (architektura 3-stopniowa, krok 29)."""
    years = ", ".join(str(y) for y in context.get("years_with_data") or []) or "brak danych"
    return (
        "Rozpoznaj intencję pytania użytkownika o WŁASNY portfel akcji Nokia Oyj "
        "(NOKIA.HE) i polskie rozliczenie podatkowe akcji pracowniczych (ESPP/LTI). "
        "Wybierz DOKŁADNIE JEDNĄ intencję z enumeracji w schemacie i wyciągnij z "
        "pytania parametry (ilość akcji, rok, cena, horyzont) — POMIŃ pole w 'params', "
        "gdy dany parametr nie pada wprost w pytaniu, NIGDY nie zgaduj wartości liczbowej.\n\n"
        f"Dziś: {context['today']}. Lata z danymi podatkowymi w systemie: {years}. "
        f"Aktywna polityka kosztu nabycia: {context.get('cost_basis_policy', 'own_only')}.\n\n"
        f"Pytanie użytkownika: {question}\n\n"
        "Jeśli pytanie nie pasuje do żadnej znanej intencji (inna spółka, pogawędka, "
        "prośba o poradę niezwiązaną z danymi w systemie), zwróć intent='inne'."
    )


def chat_narration_prompt(question: str, title: str, lines: list[dict],
                          facts: dict | None = None) -> str:
    """lines: [{'label','value','unit'}, ...] — JUŻ POLICZONE przez silnik
    Pythona. Zadanie modelu to wyłącznie narracja PL wokół tych liczb, nigdy
    ich zmiana ani dopisanie nowych (halucynacja kwoty ma być strukturalnie
    niemożliwa — liczby i tak renderuje Jinja z `lines`, nie ten tekst)."""
    lines_text = "\n".join(
        f"- {l['label']}: {l['value']} {l.get('unit', '')}".strip() for l in lines
    ) or "(brak policzonych wartości — silnik nie miał czego policzyć)"
    return (
        "Użytkownik zadał pytanie o własny portfel akcji Nokia lub polski podatek od "
        "akcji pracowniczych. Poniżej są JUŻ POLICZONE przez silnik aplikacji wartości — "
        "Twoim jedynym zadaniem jest ubrać je w naturalną, zwięzłą odpowiedź po polsku "
        "(1-3 zdania). NIE ZMIENIAJ ANI NIE DOPISUJ ŻADNEJ LICZBY — użyj dokładnie tych "
        "wartości i jednostek, które są na liście poniżej, nic więcej nie licz ani nie "
        "zgaduj.\n\n"
        f"Pytanie: {question}\n"
        f"Temat: {title}\n"
        f"Policzone wartości:\n{lines_text}\n\n"
        "Napisz odpowiedź jednym-dwoma zdaniami po polsku, bez wstępów typu 'Oto odpowiedź'."
    )


def copilot_narration_prompt(conditions: list[dict]) -> str:
    """`conditions`: `[{'title', 'lines': [{'label','value','unit'}, ...]}, ...]`
    — JUŻ POLICZONE przez ai/copilot.py (krok 33). W odróżnieniu od
    `chat_narration_prompt` to jest PROAKTYWNY push — nikt nie zadał pytania,
    więc prompt tego nie udaje (żadnej frazy „użytkownik zadał pytanie”).
    Ten sam kontrakt liczbowy i ten sam `CHAT_NARRATION_SCHEMA`: model tylko
    ubiera już policzone fakty w naturalny język, nigdy nie liczy ani nie
    zgaduje nowej liczby."""
    if not conditions:
        blocks = "(brak warunków)"
    else:
        blocks = "\n\n".join(
            f"[{c['title']}]\n" + "\n".join(
                f"- {l['label']}: {l['value']} {l.get('unit', '')}".strip()
                for l in c["lines"])
            for c in conditions
        )
    return (
        "Jesteś asystentem inwestora posiadającego akcje pracownicze Nokia Oyj "
        "(NOKIA.HE, Nasdaq Helsinki). Nikt Cię o nic nie zapytał — to Ty "
        "zauważyłeś poniższe fakty i wysyłasz krótkie, proaktywne przypomnienie "
        "na telefon. Wartości poniżej są JUŻ POLICZONE przez silnik aplikacji. "
        "NIE ZMIENIAJ ANI NIE DOPISUJ ŻADNEJ LICZBY ani daty — użyj dokładnie "
        "tych wartości i jednostek, nic więcej nie licz ani nie zgaduj.\n\n"
        f"{blocks}\n\n"
        "Napisz 1-3 zdania po polsku, spinające te sprawy w jedną wiadomość, "
        "bez wstępów typu 'Oto podsumowanie'. To nie jest porada inwestycyjna."
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
