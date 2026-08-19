"""Parser wyciągów Computershare (krok 13, BLUEPRINT §3a).

Fixture'y to RĘCZNIE NAPISANE, syntetyczne linie tekstu (nie prawdziwe pliki użytkownika —
repo miczu71/nokia_tracker jest PUBLICZNE na GitHubie, więc żadne prawdziwe dane osobowe/
finansowe nie mogą trafić do repo, patrz docs/PLAN_KROK_13.md). Kształt linii (spacing,
separator tysięcy, cienka spacja Unicode U+2009) jest dokładnie zmierzony na realnych
plikach lokalnie (test_computershare_pdf_real_files.py, bramkowany istnieniem
/config/akcje_temp/, nigdy nie commitowany)."""
from __future__ import annotations

from nokia_tracker.importers import computershare_pdf as cp

# Cienka spacja Unicode (U+2009) jako separator tysięcy — zaobserwowana w realnych plikach.
_THIN = " "


def test_parse_document_meta_extracts_period_and_as_of():
    text = "                1 Jan2026  - 26 Jul2026                     User  ID: 00000000\nas of 26 Jul2026"
    meta = cp.parse_document_meta(text)
    assert meta["period_start"] == "2026-01-01"
    assert meta["period_end"] == "2026-07-26"
    assert meta["as_of_date"] == "2026-07-26"


def test_parse_purchases_extracts_all_eleven_fields():
    line = (
        "  2 Feb  2026        24 Oct  2025         2 Feb 2026          4Feb  2026           "
        "105.39  EUR           0.00 EUR           0.00 EUR         0.00  EUR          "
        "5.48 EUR        19.21982           0.00 EUR"
    )
    rows = cp.parse_purchases(line)
    assert len(rows) == 1
    r = rows[0]
    assert r["allocation_date"] == "2026-02-02"
    assert r["contribution_date"] == "2025-10-24"
    assert r["trade_date"] == "2026-02-02"
    assert r["settlement_date"] == "2026-02-04"
    assert r["contribution_amount_eur"] == 105.39
    assert r["residual_amount_previous_eur"] == 0.0
    assert r["fees_eur"] == 0.0
    assert r["fair_market_value_eur"] == 0.0
    assert r["purchase_price_eur"] == 5.48
    assert r["quantity"] == 19.21982
    assert r["residual_amount_eur"] == 0.0


def test_parse_purchases_ignores_annotation_and_footer_lines():
    text = (
        "0.234203 PLN/EUR\n"
        "                      Participant                                    450.00 PLN\n"
        "Purchases                                                1  Jan   2022     to  1  Jan   2023\n"
        "Nokia Share: 4.327EURasof30Dec2022  (HelsinkiSE)\n"
    )
    assert cp.parse_purchases(text) == []


def test_parse_matching_shares_multiple_rows_in_one_grant():
    text = (
        f"Matching   Shares                                                      27 Oct 2025                      1 Aug  2026                  27 Aug  2026                             29.24                     1{_THIN}036.84  PLN\n"
        f"Matching   Shares                                                       2 Feb 2026                      1 Aug  2026                  27 Aug  2026                             28.99                     1{_THIN}027.75  PLN\n"
        "Vested  Matching   Shares                                             28 Aug  2025                        3.71 EUR                      4.51 EUR                               0.48                       16.98 PLN\n"
    )
    rows = cp.parse_matching_shares(text)
    assert len(rows) == 2  # "Vested Matching Shares" (kształt inny - suma, nie transza) pominięty
    assert rows[0]["allocation_date"] == "2025-10-27"
    assert rows[0]["vesting_date"] == "2026-08-01"
    assert rows[0]["available_from"] == "2026-08-27"
    assert rows[0]["quantity"] == 29.24
    assert rows[0]["estimated_value_pln"] == 1036.84


def test_parse_vested_matching_shares_basic_row():
    line = (
        "Vested  Matching   Shares                                             28 Aug  2025"
        "                        3.71 EUR                      4.51 EUR                               "
        "0.48                       16.98 PLN"
    )
    rows = cp.parse_vested_matching_shares(line)
    assert len(rows) == 1
    r = rows[0]
    assert r["vested_date"] == "2025-08-28"
    assert r["cost_basis_eur"] == 3.71
    assert r["gain_per_share_eur"] == 4.51
    assert r["quantity"] == 0.48
    assert r["estimated_value_pln"] == 16.98


def test_parse_vested_matching_shares_negative_gain_per_share():
    # Gain per share bywa ujemny, gdy cena spadła od dnia vestingu do dnia wyciągu.
    line = (
        "Vested  Matching  Shares                                            30 Aug  2023"
        "                       3.65 EUR                     -0.60 EUR                             "
        "8.21                     109.12  PLN"
    )
    rows = cp.parse_vested_matching_shares(line)
    assert len(rows) == 1
    assert rows[0]["gain_per_share_eur"] == -0.60
    assert rows[0]["quantity"] == 8.21


def test_parse_vested_matching_shares_multiple_rows_same_date_and_price():
    # Realny przypadek: kilka sub-lotów z tej samej kohorty vestingu, ta sama data/cena,
    # różne ilości - każdy musi się sparsować jako osobny wiersz.
    text = (
        "Vested  Matching  Shares                                            30 Aug  2023"
        "                       3.65 EUR                     -0.60 EUR                             "
        "8.21                     109.12  PLN\n"
        "Vested  Matching  Shares                                            30 Aug  2023"
        "                       3.65 EUR                     -0.60 EUR                             "
        "7.20                      95.65  PLN\n"
    )
    rows = cp.parse_vested_matching_shares(text)
    assert len(rows) == 2
    assert {r["quantity"] for r in rows} == {8.21, 7.20}
    assert all(r["vested_date"] == "2023-08-30" for r in rows)


def test_parse_vested_matching_shares_ignores_vested_dividend_shares():
    # "Vested Dividend Shares" ma identyczny kształt kolumn, ale to inna kategoria (już
    # pokryta przez parse_dividends/dividend_drip) - regex musi ją świadomie pomijać.
    line = (
        "Vested  Dividend   Shares                                             13 Nov  2025"
        "                        6.06 EUR                      2.16 EUR                               "
        "2.52                       89.50 PLN"
    )
    assert cp.parse_vested_matching_shares(line) == []


# --- krok 19: Vested Dividend Shares jako źródło zapasowe dla wyciągów 2022-2024,
# które nie mają sekcji "Dividend (Reinvested)" transakcyjnej (ta pojawia się dopiero
# od wyciągu 2025) — bez tego akcje z reinwestowanej dywidendy sprzed 2025 nigdy nie
# stają się lotami, a sekcja G PIT-38 tych lat wychodzi zerowa mimo realnych dywidend.

def test_parse_vested_dividend_shares_basic_row():
    line = (
        "Vested  Dividend  Shares                                            20 Feb  2023"
        "                       4.48 EUR                     -1.43 EUR                             "
        "0.04                        0.56 PLN"
    )
    rows = cp.parse_vested_dividend_shares(line)
    assert len(rows) == 1
    r = rows[0]
    assert r["vested_date"] == "2023-02-20"
    assert r["cost_basis_eur"] == 4.48
    assert r["gain_per_share_eur"] == -1.43
    assert r["quantity"] == 0.04
    assert r["estimated_value_pln"] == 0.56


def test_parse_vested_dividend_shares_multiple_rows_same_date():
    # Realny przypadek: dwie transze tej samej dywidendy tego samego dnia (różne ilości).
    text = (
        "Vested  Dividend  Shares                                            13 Nov  2023"
        "                       3.28 EUR                     -0.23 EUR                             "
        "0.38                        5.06 PLN\n"
        "Vested  Dividend  Shares                                            13 Nov  2023"
        "                       3.28 EUR                     -0.23 EUR                             "
        "0.19                        2.51 PLN\n"
    )
    rows = cp.parse_vested_dividend_shares(text)
    assert len(rows) == 2
    assert {r["quantity"] for r in rows} == {0.38, 0.19}
    assert all(r["vested_date"] == "2023-11-13" for r in rows)


def test_parse_vested_dividend_shares_ignores_vested_matching_shares():
    # Symetria testu odwrotnego (test_parse_vested_matching_shares_ignores_vested_dividend_shares)
    # - kształt kolumn identyczny, kategoria inna, regex musi rozróżniać po etykiecie.
    line = (
        "Vested  Matching   Shares                                             28 Aug  2025"
        "                        3.71 EUR                      4.51 EUR                               "
        "0.48                       16.98 PLN"
    )
    assert cp.parse_vested_dividend_shares(line) == []


def test_parse_rs_award_multiple_tranches_same_grant():
    text = (
        f"2025  RS AWARD    07-JUL-2025                                         7Jul 2025                     5 Jul 2028                   5 Jul2028                          633.00                  14{_THIN}882.25  PLN\n"
        f"2025  RS AWARD    07-JUL-2025                                         7Jul 2025                     5 Jul 2027                   5 Jul2027                          633.00                  14{_THIN}882.25  PLN\n"
        f"2025       RS AWARD             07-JUL-2025                                                                                                                                 44{_THIN}670.26          PLN\n"
    )
    rows = cp.parse_rs_award(text)
    assert len(rows) == 2  # wiersz sumy (bez dat/ilości) poprawnie pominięty
    assert all(r["participation_description"] == "2025 RS AWARD 07-JUL-2025" for r in rows)
    assert rows[0]["vesting_date"] == "2028-07-05"
    assert rows[1]["vesting_date"] == "2027-07-05"
    assert rows[0]["quantity"] == 633.0


def test_parse_rs_award_handles_full_month_name_in_label():
    text = (
        f"2023  RS AWARD    06-JULY-2023                                         6 Jul2023                      6 Jul2026                     9Jul 2026                        2{_THIN}100.00                   49{_THIN}372.40  PLN\n"
    )
    rows = cp.parse_rs_award(text)
    assert len(rows) == 1
    assert rows[0]["participation_description"] == "2023 RS AWARD 06-JULY-2023"
    assert rows[0]["quantity"] == 2100.0


def test_parse_dividends_extracts_all_ten_fields():
    line = (
        "30 Jan 2026                       19 Feb  2026            61.491555                  1.84 EUR        0.64 EUR       0.00 EUR            "
        "1.20 EUR      6.3015  EUR          0.19028           0.00 EUR"
    )
    rows = cp.parse_dividends(line)
    assert len(rows) == 1
    r = rows[0]
    assert r["record_date"] == "2026-01-30"
    assert r["purchase_date"] == "2026-02-19"
    assert r["entitled_quantity"] == 61.491555
    assert r["gross_dividend_payment_eur"] == 1.84
    assert r["taxes_eur"] == 0.64
    assert r["fees_eur"] == 0.0
    assert r["dividend_reinvested_eur"] == 1.20
    assert r["purchase_price_eur"] == 6.3015
    assert r["purchased_shares"] == 0.19028
    assert r["residual_amount_eur"] == 0.0


def test_parse_dividends_entitled_quantity_without_decimal_point_lti_whole_shares():
    # Wiersz z realnego wyciągu 2026-08-19: dywidenda reinwestowana od transzy LTI
    # (RS Award, 2734 akcji vestowanych 2026-07-09) - "Entitled Quantity" to okrągła
    # liczba akcji BEZ kropki dziesiętnej (w przeciwieństwie do ułamkowych ilości ESPP),
    # więc _NUM (kropka wymagana) nie dopasowuje całego wiersza i cała dywidenda
    # (109.36 EUR brutto, 7.81916 akcji reinwestowanych) po cichu znikała z importu -
    # to właśnie dało konflikt salda "balance" na produkcji (~7,82 akcji luki).
    line = (
        "24 Jul2026                        13  Aug 2026                  2734               "
        "109.36 EUR        38.27 EUR       0.00 EUR           71.08  EUR       9.091 EUR          "
        "7.81916            0.00 EUR"
    )
    rows = cp.parse_dividends(line)
    assert len(rows) == 1
    r = rows[0]
    assert r["record_date"] == "2026-07-24"
    assert r["purchase_date"] == "2026-08-13"
    assert r["entitled_quantity"] == 2734.0
    assert r["gross_dividend_payment_eur"] == 109.36
    assert r["taxes_eur"] == 38.27
    assert r["fees_eur"] == 0.0
    assert r["dividend_reinvested_eur"] == 71.08
    assert r["purchase_price_eur"] == 9.091
    assert r["purchased_shares"] == 7.81916
    assert r["residual_amount_eur"] == 0.0


# --- krok 19: kontrola krzyżowa salda (BLUEPRINT §3a) — "Shares" na stronie 1 wyciągu
# (sekcja "Assets by type") vs SUM(qty_remaining) w bazie.

def test_parse_shares_total_basic():
    # Kształt zmierzony na realnym wyciągu: liczba na linii BEZPOŚREDNIO PRZED linią
    # "Shares", w tej samej pozycji kolumnowej (layout mode zachowuje wyrównanie).
    text = (
        " 61.491555                                                           90.735665\n"
        f" Shares                                      1{_THIN}445.71 PLN            "
        f"Share  inSuccess  Plan 2019-2026             2{_THIN}133.26 PLN\n"
    )
    assert cp.parse_shares_total(text) == 61.491555


def test_parse_shares_total_single_column_variant():
    # Wyciąg z małym portfelem (2022): tylko jedna kolumna na tej linii, bez pary planu.
    text = (
        " 14.657496                                                          21.986244\n"
        " Shares                                       297.93 PLN            Share in "
        "Success Plan 2019-2026              446.89  PLN\n"
    )
    assert cp.parse_shares_total(text) == 14.657496


def test_parse_shares_total_ignores_shares_total_column_header_far_in_document():
    # "Shares" pojawia się też jako nagłówek kolumny w tabelach "Available for trading"
    # (daleko po prawej, duży wcięcie) - to NIE jest suma z Assets by type, regex musi
    # ją ignorować (wymaga małego wcięcia).
    text = (
        " " * 140 + "Shares                       Total\n"
        "    Available        for  trading\n"
        "                                            163.187488               2 169.16 PLN\n"
    )
    assert cp.parse_shares_total(text) is None


def test_parse_shares_total_returns_none_when_absent():
    assert cp.parse_shares_total("brak żadnej sekcji Assets by type tutaj") is None


def test_parse_withhold_to_cover_type_a_zero_effect_confirmation():
    line = "9 Jul2026                                            Nokia  Share                            634                 10.22 EUR                 0.00 EUR                   0.00 EUR                     634"
    type_a, type_b = cp.parse_withhold_to_cover(line)
    assert len(type_a) == 1
    assert len(type_b) == 0
    assert type_a[0]["quantity"] == type_a[0]["net_units"] == 634.0


def test_parse_withhold_to_cover_type_a_large_quantity_no_thousands_separator():
    # "2100" bez separatora tysięcy mimo 4 cyfr - zaobserwowane empirycznie.
    line = "9 Jul2026                                            Nokia  Share                           2100                 10.22 EUR                 0.00 EUR                   0.00 EUR                    2100"
    type_a, _ = cp.parse_withhold_to_cover(line)
    assert len(type_a) == 1
    assert type_a[0]["quantity"] == 2100.0


def test_parse_withhold_to_cover_type_b_is_a_real_sale_never_zero_effect():
    line = "27 Oct 2025                                           784                               5.31 EUR              4 161.47 EUR                 0.00 EUR                    8.32 EUR           4 153.15  EUR"
    type_a, type_b = cp.parse_withhold_to_cover(line)
    assert len(type_a) == 0
    assert len(type_b) == 1
    b = type_b[0]
    assert b["quantity"] == 784.0
    assert b["net_proceeds_eur"] == 4153.15
    # Arytmetyka: Sale Proceeds - Taxes - Fees == Net proceeds
    assert round(b["sale_proceeds_eur"] - b["taxes_eur"] - b["fees_eur"], 2) == b["net_proceeds_eur"]


def test_parse_withhold_to_cover_returns_both_types_from_mixed_text():
    text = (
        "9 Jul2026                                            Nokia  Share                            634                 10.22 EUR                 0.00 EUR                   0.00 EUR                     634\n"
        "27 Oct 2025                                           784                               5.31 EUR              4 161.47 EUR                 0.00 EUR                    8.32 EUR           4 153.15  EUR\n"
    )
    type_a, type_b = cp.parse_withhold_to_cover(text)
    assert len(type_a) == 1
    assert len(type_b) == 1
