"""Trasy dywidend: /dividends, /dividends/harmonogram, /api/preview/dividend."""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for

from ._context import AppContext
from ._helpers import _is_future_date
from .. import __version__
from .. import db as dbm
from .. import dividend_outlook as outlookm
from .. import settings as settingsm
from ..providers import fx_nbp
from ..tax import dividends as taxdiv
from ..tax import trace as taxtrace
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

    @app.post("/dividends")
    def dividends_post():
        """Krok 16: przechodzi przez `taxdiv.add_dividend()` — jedyne miejsce
        zapisu dywidend (import PDF i formularz ręczny razem), więc kurs NBP
        zamrożony na Record Date i (opcjonalny) lot DRIP powstają identycznie
        niezależnie od źródła wpisu. Formularz nadal przyjmuje procent u
        źródła (nie kwotę), więc przeliczamy go na `taxes_eur` przed
        wywołaniem — `add_dividend` sam odtworzy ten sam procent z
        `taxes_eur/gross_eur`."""
        conn = _conn()
        try:
            pay_date = request.form.get("pay_date") or ""
            if _is_future_date(pay_date):
                return redirect(url_for(
                    "dividends_get", error="Data wypłaty nie może być w przyszłości "
                                           "(NBP nie publikuje kursów na przyszłe daty)"))
            drip_purchase_date = request.form.get("drip_purchase_date") or None
            if drip_purchase_date and _is_future_date(drip_purchase_date):
                return redirect(url_for(
                    "dividends_get", error="Data reinwestycji nie może być w przyszłości"))

            cfg = settingsm.get_settings(conn)
            gross_eur = float(request.form.get("gross_eur") or 0)
            quantity = float(request.form.get("quantity") or 0) or None
            gross_per_share = float(request.form.get("gross_per_share_eur") or 0) or None
            withholding_raw = request.form.get("withholding_pct")
            withholding_pct = (float(withholding_raw) if withholding_raw
                               else cfg["finnish_withholding_pct"])
            taxes_eur = gross_eur * withholding_pct / 100

            drip_price_raw = request.form.get("drip_price_eur")
            drip_shares_raw = request.form.get("drip_shares")
            purchase_price_eur = float(drip_price_raw) if drip_price_raw else None
            purchased_shares = float(drip_shares_raw) if drip_shares_raw else None

            # Klucz deterministyczny na treści formularza (nie na czasie zapisu):
            # przypadkowy podwójny submit tego samego wpisu jest teraz idempotentny
            # (poprawa względem starego surowego INSERT-a, który dublował wiersz).
            natural_key = f"manual:{pay_date}:{gross_eur}:{quantity or 0}:{withholding_pct}"

            with dbm.WRITE_LOCK:
                taxdiv.add_dividend(
                    conn, record_date=pay_date, entitled_quantity=quantity or 0.0,
                    gross_eur=gross_eur, taxes_eur=taxes_eur,
                    gross_per_share_eur=gross_per_share,
                    purchase_date=drip_purchase_date, purchase_price_eur=purchase_price_eur,
                    purchased_shares=purchased_shares, natural_key=natural_key)
            return redirect(url_for("dividends_get", saved="1"))
        finally:
            conn.close()

    @app.get("/api/preview/dividend")
    def preview_dividend():
        conn = _conn()
        try:
            pay_date = request.args.get("pay_date") or ""
            if not pay_date:
                return {"ok": False, "error": "Podaj datę wypłaty."}
            if _is_future_date(pay_date):
                return {"ok": False, "error": "Data wypłaty nie może być w przyszłości "
                                              "(NBP nie publikuje kursów na przyszłe daty)."}
            try:
                gross_eur = float(request.args.get("gross_eur") or 0)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}
            if gross_eur <= 0:
                return {"ok": False, "error": "Podaj kwotę brutto większą od zera."}

            cfg = settingsm.get_settings(conn)
            withholding_raw = request.args.get("withholding_pct")
            withholding_pct = (float(withholding_raw) if withholding_raw
                               else cfg["finnish_withholding_pct"])

            rate = fx_nbp.rate_for_event(conn, pay_date)
            if rate is None:
                return {"ok": False,
                        "error": f"Brak kursu NBP dla dnia {pay_date} (spróbuj ponownie później)."}
            nbp_rate, nbp_rate_date = rate
            gross_pln = gross_eur * nbp_rate
            tax = taxdiv.compute_dividend_tax_pln(
                {"gross_pln": gross_pln, "withholding_pct": withholding_pct}, cfg)
            deriv = taxtrace.fx_derivation(conn, pay_date, nbp_rate, nbp_rate_date, "dywidenda")

            lines = [
                {"label": "Brutto", "value": round(gross_pln, 2), "unit": "PLN"},
                {"label": "Pobrane u źródła", "value": tax["withholding_paid_pln"], "unit": "PLN"},
                {"label": "Belka (19%)", "value": tax["belka_pln"], "unit": "PLN"},
                {"label": "Dopłata w PL", "value": tax["pl_tax_due_pln"], "unit": "PLN",
                 "emphasis": True},
                {"label": "Do odzyskania z Vero", "value": tax["reclaimable_from_finland_pln"],
                 "unit": "PLN"},
            ]

            drip_shares_raw = request.args.get("drip_shares")
            drip_price_raw = request.args.get("drip_price_eur")
            drip_date = request.args.get("drip_purchase_date")
            if drip_shares_raw and drip_price_raw and drip_date:
                try:
                    drip_shares = float(drip_shares_raw)
                    drip_price = float(drip_price_raw)
                    lines.append({
                        "label": "Powstanie lot",
                        "value": f"{drip_shares:.4f} akcji @ {drip_price:.4f} EUR ({drip_date})",
                        "unit": None,
                    })
                except ValueError:
                    pass

            return {
                "ok": True,
                "nbp_rate": nbp_rate,
                "nbp_rate_date": nbp_rate_date,
                "explanation_pl": deriv["explanation_pl"],
                "table_urls": deriv.get("urls"),
                "lines": lines,
            }
        finally:
            conn.close()
