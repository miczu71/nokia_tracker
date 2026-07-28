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
