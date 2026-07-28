# Nokia Tracker — domknięcie luk po imporcie PDF (Portfel z lotów, prefilled sprzedaż, widoczność grantów/LTI)

## Context

Użytkownik zaimportował 5 realnych wyciągów Computershare przez `/imports` (krok 13) i zgłosił,
że „dane w innych zakładkach się nie zmieniły". Zdiagnozowałem to empirycznie (bez zmian w kodzie):
import faktycznie się powiódł (46 wierszy, 1 konflikt), `/lots` i `/dividends` oraz sensory MQTT
lotów poprawnie odzwierciedlają zaimportowane dane. Znalazłem trzy realne luki, które użytkownik
potwierdził chce zamknąć w jednym zestawie prac:

1. **Karta „Portfel" na Pulpicie i strona `/portfolio`** wciąż pokazują ręcznie wpisywaną liczbę
   (`position_qty`/`avg_cost_eur` z ustawień, krok 9) zamiast rzeczywistego stanu z tabeli `lots`.
2. **Sprzedaż z 27.10.2025 znaleziona, ale wymaga niewygodnego ręcznego przepisywania.** Parser
   poprawnie wykrył sekcję PDF-a nazwaną „Sell (Shares)" (784 akcje, 5,31 EUR/szt, prowizja 8,32 EUR,
   wpływ netto 4153,15 EUR) i odłożył ją do `import_conflicts` (świadomie nie księguje automatycznie
   — to prawdziwa gotówkowa sprzedaż). Użytkownik chce, żeby taki wpis pojawiał się **od razu
   wypełniony danymi z PDF-a**, gotowy do zatwierdzenia jednym kliknięciem — nie do ręcznego
   przepisywania liczb do osobnego formularza `/lots/sell`.
3. **Brak jakiejkolwiek widoczności grantów ESPP/LTI i harmonogramu vestingu.** Zweryfikowałem
   lokalnie: import poprawnie zapisał do bazy 6 grantów ESPP + 6 transz oraz 2 granty LTI + 4 transze
   (2100 @ 2026-07-06; 634/633/633 @ 2026/2027/2028) — logi Supervisora z okna importu nie pokazują
   żadnego wyjątku. Dane są w bazie, ale krok 13 nie zbudował żadnej strony ani sensora, żeby je
   pokazać (naturalnie należało to raczej do kroku 14, ale użytkownik chce to mieć teraz).

Wszystkie trzy elementy mają wejść do jednego wdrożenia.

## 1. Portfel z lotów (Pulpit + `/portfolio`)

**Nowa funkcja** `portfolio.py::lots_based_position_values(conn, cfg, price_eur, eurpln_rate,
dividends_net_total_eur)`:
- `open = tax.lots.open_lots(conn)` — wszystkie otwarte loty, każdego typu.
- `total_qty = sum(qty_remaining)` **po wszystkich typach** (`own`/`matched`/`lti`/`dividend_drip`)
  — to fizyczna liczba posiadanych akcji, niezależnie od polityki podatkowej.
- Koszt bazowy liczony **wg aktywnej polityki** (`cfg['cost_basis_policy']`, ten sam mechanizm co
  `sensors.py::lots_values()` z kroku 12): `avg_cost_eur` = średnia ważona `price_eur` lotów
  należących do `tax.policy.POLICIES[aktywna_polityka]`, `cost_basis_eur` = suma odpowiednio.
  **Świadomy efekt uboczny, nie błąd:** przy `own_only` (domyślne) loty `matched`/`lti`/
  `dividend_drip` wnoszą pełną wartość rynkową, ale zero kosztu — niezrealizowany zysk będzie
  wyglądał na wyższy niż z samych zakupów. To poprawnie odzwierciedla odroczone opodatkowanie
  (dostałeś je za darmo lub prawie za darmo) — dokładnie ta sama zasada co w tabeli „Trzy polityki
  kosztu" na `/lots`.
- Reużywa istniejącej `portfolio.position_values(qty, avg_cost_eur, price_eur, eurpln_rate,
  dividends_net_total_eur)` (krok 9, bez zmian) — tylko podstawia inne wejścia zamiast
  `cfg['position_qty']`/`cfg['avg_cost_eur']`.

**`main.py::publish_sensors` i `web.py::dashboard`/`portfolio_get`:** przełączyć na
`lots_based_position_values`, gdy `tax.lots.open_lots(conn)` zwraca cokolwiek; **fallback na starą
`position_values(cfg['position_qty'], ...)` gdy lotów zero** (użytkownik, który nic jeszcze nie
zaimportował/nie dodał lotów, nie traci możliwości ręcznego wpisania orientacyjnej liczby).

**`/portfolio`:** formularz ręcznego wpisania zostaje (jako fallback), ale strona dodatkowo pokazuje
read-only podsumowanie z lotów, gdy istnieją, z wyraźnym oznaczeniem które źródło jest aktywne.

## 2. Prefilled potwierdzenie sprzedaży w kolejce konfliktów

**Nowa trasa** `POST /imports/conflicts/<id>/confirm-sale`:
- Czyta `import_conflicts.incoming_json` (już zawiera `execution_date`/`quantity`/`sale_price_eur`/
  `fees_eur` dokładnie w kształcie, jakiego oczekuje `tax.lots.record_sale`).
- Woła `taxlots.record_sale(conn, sale_date=execution_date, quantity=quantity,
  price_eur=sale_price_eur, fee_eur=fees_eur)` pod `db.WRITE_LOCK`.
- Sukces: oznacza konflikt `resolved=1`, `resolution=f"zaksięgowano automatycznie jako sprzedaż
  (sale_id={sale_id})"`, redirect z komunikatem powodzenia.
- `InsufficientLotsError`/`CostBasisMissingError`: **nie** oznacza konfliktu jako rozwiązany,
  redirect z czytelnym błędem (ten sam wzorzec co `/lots/sell` z kroku 12).

**`templates/imports.html`:** dla wierszy `entity_type == 'withhold_to_cover_sale'` — zamiast (albo
obok) generycznego pola tekstowego, pokazać czytelnie sparsowane dane (Data/Ilość/Cena/Prowizja/
Wpływ netto) wyciągnięte z `incoming_json` oraz przycisk **„Zatwierdź jako sprzedaż"** (POST do
nowej trasy, zero przepisywania liczb). Generyczna trasa `resolve` (ręczna notatka) zostaje dla
pozostałych typów konfliktów (np. wartość lotu się nie zgadza).

## 3. Widoczność grantów ESPP/LTI i harmonogramu vestingu

**Nowa trasa** `GET /grants` (osobna podstrona, wpis w nawigacji między „Loty" a „Dywidendy"):
- ESPP: `SELECT g.*, v.* FROM grants g JOIN vests v ON v.grant_id = g.id WHERE g.program='espp'`
  — jeden grant = jedna transza, prosta tabela (Data przyznania, Data vestingu, Ilość, Status).
- LTI: te same dane, ale grupowane per `participation_description` — nagłówek grantu +
  zagnieżdżone transze, z sumą `SUM(v.quantity)` per grant (bo `grants.quantity` celowo `NULL` dla
  LTI od kroku 13 — pojedynczy wiersz RS AWARD nie zna sumy całego grantu).
- Kolumna „Status" wprost z `vests.status` (`pending`/`vested`/`cancelled`) — dziś wszystko
  `pending`, bo scheduler auto-vestingu (krok 14) jeszcze nie istnieje; strona jest czysto
  odczytowa, nie tworzy lotów.

**2 nowe sensory MQTT** (analogicznie do reszty encji lotów z kroku 12): `unvested_qty` (suma
`quantity` wszystkich `vests` o statusie `pending`), `next_vest_date` (najbliższa `vest_date` w
przyszłości, z atrybutem `next_vest_qty`) — dokładnie te, które blueprint już przewidywał dla
przyszłego kroku 14, więc nie trzeba ich będzie dodawać drugi raz.

## Weryfikacja

1. `pytest` — nowe testy: `portfolio.lots_based_position_values` (loty różnych typów, polityka
   filtruje koszt, fallback gdy brak lotów), `/imports/conflicts/<id>/confirm-sale` (sukces + oba
   błędy FIFO, konflikt nieoznaczony przy błędzie), `/grants` (puste/wypełnione, wielotranszowy LTI
   grupowany poprawnie), nowe sensory `unvested_qty`/`next_vest_date`.
2. Deploy live (ten sam cykl bez bumpa wersji).
3. **Rzeczywista weryfikacja na żywo:** po wdrożeniu użytkownik klika „Zatwierdź jako sprzedaż" na
   konflikcie 784 akcji — sprawdzamy, że `realized_income_pln`/`realized_tax_pln` i Pulpit/Portfel
   faktycznie się zmieniają, `/grants` pokazuje 6 ESPP + 2 LTI (4 transze), konflikt znika z kolejki.
