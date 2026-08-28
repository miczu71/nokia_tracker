"""Trasy dywidend: /dividends, /dividends/harmonogram.

Etap 3 (docs/PLAN_0_28_0_ui_porzadki.md): dywidendy trafiają do bazy
wyłącznie z wyciągów Computershare (`importers/computershare_pdf.py`) —
ręczny formularz (`POST /dividends`, `GET /api/preview/dividend`) został
usunięty. Harmonogram (`/dividends/harmonogram`) zostaje — to prognoza z
ogłoszenia WZA, nie dane z wyciągu."""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for

from ._context import AppContext
from .. import __version__
from .. import db as dbm
from .. import dividend_outlook as outlookm
from .. import settings as settingsm
from ..tax import dividends as taxdiv
from ..views.dividends import dividends_view
from ..views.market_context import latest_eurpln_rate


def register_dywidendy_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn

    @app.get("/dividends")
    def dividends_get():
        """Krok 16: JEDNO źródło prawdy z `add_dividend()` — kwoty w PLN na kursie
        NBP zamrożonym na Record Date (`compute_dividend_tax_pln`, ten sam
        mechanizm co `/pit38`), nie osobny kalkulator EUR na bieżących stawkach
        jak przed ujednoliceniem formularza. `backfill_missing_dividend_rates`
        dogania dywidendy wpisane ręcznie przed tym krokiem (surowy INSERT
        wtedy nie zamrażał kursu).

        Krok 18: `totals` liczone SUMOWANIEM `items` (a nie osobnym wywołaniem
        `sensors.dividends_values`) — przed tą zmianą strona pokazywała dwie
        niezgodne matematyki 40px od siebie: kafelki na kursach BIEŻĄCYCH w EUR
        (`sensors.dividends_values`), tabela pod nimi na kursach NBP ZAMROŻONYCH
        na Record Date w PLN (`compute_dividend_tax_pln`, ten sam co tu). Zero
        nowych zapytań do NBP — `items` już ma policzone `*_pln` per wiersz.
        `sensors.dividends_values` zostaje nietknięte dla sensorów MQTT i dla
        linii dywidend na pulpicie (tam liczone po kursie bieżącym, spójnie z
        resztą pulpitu — patrz krok 2)."""
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            taxdiv.backfill_missing_dividend_rates(conn)
            # Krok 30 (docs/PLAN_KROK_30_dywidendy.md): `reconcile_schedule` dotyka
            # tylko rat jeszcze niedopasowanych (indeks na `record_date`, tania
            # operacja) — pod WRITE_LOCK, mimo że `backfill_missing_dividend_rates`
            # wyżej nie jest (przedkrokowy stan, nie naprawiany tutaj, ale nowy
            # zapis dostaje właściwy kontrakt od razu). Czyta/zapisuje wyłącznie
            # `dividend_schedule`, nigdy `dividends` — bezpieczne przed odczytem
            # `items` w `dividends_view` poniżej.
            with dbm.WRITE_LOCK:
                outlookm.reconcile_schedule(conn)
            lata_raw = request.args.get("lata")
            years_ahead = int(lata_raw) if lata_raw in ("1", "3", "5") else 3
            eurpln_rate = latest_eurpln_rate(conn)
            view = dividends_view(conn, cfg, years_ahead, eurpln_rate)

            return render_template(
                "dividends.html", active="dividends", version=__version__,
                cfg=cfg, saved=request.args.get("saved") == "1",
                error=request.args.get("error"), years_ahead=years_ahead, **view)
        finally:
            conn.close()

    @app.post("/dividends/harmonogram")
    def dividend_schedule_post():
        """Krok 30: jedno ogłoszenie WZA = jeden formularz, do 4 rat naraz. Puste raty
        (pola bez `record_date`/`per_share`) są pomijane, nie zapisywane jako zera —
        WZA nie zawsze uchwala od razu wszystkie 4 daty. Świadomie BEZ
        `_is_future_date` — daty przyszłe są całym sensem tej tabeli (harmonogram
        dotyczy wypłat, które jeszcze się nie odbyły), a `dividend_schedule` nigdy
        nie dotyka NBP, więc walidacja stworzona dla `/lots`/`/dividends` tu by tylko
        po cichu wyłączyła funkcję."""
        conn = _conn()
        try:
            fiscal_year_raw = request.form.get("fiscal_year")
            try:
                fiscal_year = int(fiscal_year_raw)
            except (TypeError, ValueError):
                return redirect(url_for(
                    "dividends_get", error="Podaj rok obrotowy harmonogramu"))
            announced_on = request.form.get("announced_on") or None

            saved_any = False
            with dbm.WRITE_LOCK:
                for instalment in range(1, 5):
                    record_date = request.form.get(f"record_date_{instalment}")
                    per_share_raw = request.form.get(f"per_share_{instalment}")
                    if not record_date or not per_share_raw:
                        continue
                    payment_date = request.form.get(f"payment_date_{instalment}") or None
                    confirmed = bool(request.form.get(f"confirmed_{instalment}"))
                    outlookm.add_instalment(
                        conn, fiscal_year=fiscal_year, instalment=instalment,
                        record_date=record_date, gross_per_share_eur=float(per_share_raw),
                        payment_date=payment_date, dates_confirmed=confirmed,
                        announced_on=announced_on)
                    saved_any = True

            if not saved_any:
                return redirect(url_for(
                    "dividends_get",
                    error="Wypełnij co najmniej jedną ratę harmonogramu (data + stawka)"))
            return redirect(url_for("dividends_get", saved="1"))
        finally:
            conn.close()

    @app.post("/dividends/harmonogram/<int:schedule_id>/delete")
    def dividend_schedule_delete(schedule_id: int):
        conn = _conn()
        try:
            with dbm.WRITE_LOCK:
                outlookm.delete_instalment(conn, schedule_id)
            return redirect(url_for("dividends_get", saved="1"))
        finally:
            conn.close()
