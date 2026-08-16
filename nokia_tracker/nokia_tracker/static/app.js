/* Nokia Tracker — minimalny JS: wykres kursu na dashboardzie z konfigurowalnym
   zakresem (krok 16, docs/PLAN_KROK_16_transparentnosc.md). */
window.NT = (function () {
  function isDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function formatLabel(ts, granularity) {
    const d = new Date(ts);
    if (granularity === "intraday") {
      return d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit", year: "2-digit" });
  }

  function initPriceChart(canvasId, rangeBarId, apiUrl, defaultRange) {
    const el = document.getElementById(canvasId);
    const bar = document.getElementById(rangeBarId);
    if (!el || !window.Chart || !apiUrl) return;

    const storageKey = "nt.chart.range";
    let range = (window.localStorage && localStorage.getItem(storageKey)) || defaultRange;
    let chart = null;

    function setActiveButton() {
      if (!bar) return;
      bar.querySelectorAll("button").forEach((b) => {
        b.classList.toggle("active", b.dataset.range === range);
      });
    }

    function render(points, granularity) {
      const labels = points.map((p) => formatLabel(p[0], granularity));
      const data = points.map((p) => p[1]);
      if (chart) {
        chart.data.labels = labels;
        chart.data.datasets[0].data = data;
        chart.update();
        return;
      }
      chart = new Chart(el.getContext("2d"), {
        type: "line",
        data: {
          labels,
          datasets: [{
            data,
            borderColor: cssVar("--series-1"),
            backgroundColor: cssVar("--series-1") + "22",
            fill: true,
            pointRadius: 0,
            tension: 0.15,
            borderWidth: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: {
              display: true,
              ticks: { color: cssVar("--muted"), maxRotation: 0, autoSkip: true },
              grid: { display: false },
            },
            y: { ticks: { color: cssVar("--muted") }, grid: { color: cssVar("--grid") } },
          },
        },
      });
    }

    function load() {
      setActiveButton();
      fetch(apiUrl + "?range=" + encodeURIComponent(range))
        .then((r) => r.json())
        .then((data) => render(data.points || [], data.granularity))
        .catch(() => {});
    }

    if (bar) {
      bar.addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-range]");
        if (!btn) return;
        range = btn.dataset.range;
        if (window.localStorage) localStorage.setItem(storageKey, range);
        load();
      });
    }

    load();
  }

  // Krok 17: rejestr sprzedaży (/sales) — wiersz-nagłówek `.row-toggle` przełącza
  // widoczność wiersza-detalu `.row-detail` wskazanego przez `data-target`/id.
  // Detal jest już wyrenderowany serwerowo (patrz _alloc_detail.html) — to tylko
  // pokazanie/ukrycie, żadnego doładowywania.
  function initRowToggles() {
    function toggle(row) {
      const detail = row.dataset.target && document.getElementById(row.dataset.target);
      if (!detail) return;
      const expanded = row.getAttribute("aria-expanded") === "true";
      row.setAttribute("aria-expanded", String(!expanded));
      detail.hidden = expanded;
      const chevron = row.querySelector(".chevron");
      if (chevron) chevron.textContent = expanded ? "▸" : "▾";
    }

    document.addEventListener("click", (ev) => {
      const row = ev.target.closest(".row-toggle");
      if (row) toggle(row);
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const row = ev.target.closest(".row-toggle");
      if (!row) return;
      ev.preventDefault();
      toggle(row);
    });
  }

  // Krok 18: podgląd na żywo pod formularzem (dywidenda/lot/sprzedaż) — patrz
  // /api/preview/* w web.py. `config.fields` mapuje NAZWĘ POLA FORMULARZA (jak
  // jest dziś w `name=`, nietknięta — POST-y jej używają) na NAZWĘ PARAMETRU
  // API, bo np. formularz sprzedaży na /lots nazywa pola `sale_quantity` itd.,
  // a endpoint podglądu (dzielony z /pit38 „co jeśli") oczekuje `quantity`.
  // Degraduje się czysto: `.preview-box` startuje `hidden`; bez JS formularz
  // działa dokładnie jak przed krokiem 18 — żadnej blokady przycisku „Dodaj”.
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function initFormPreview(config) {
    const form = document.getElementById(config.formId);
    const box = document.getElementById(config.boxId);
    if (!form || !box) return;

    let timer = null;
    let controller = null;

    function collect() {
      const params = new URLSearchParams();
      let ready = true;
      config.fields.forEach(([formName, apiName]) => {
        const el = form.elements.namedItem(formName);
        const val = el ? el.value.trim() : "";
        if (val) params.set(apiName, val);
        if ((config.requiredFields || []).includes(formName) && !val) ready = false;
      });
      return { params, ready };
    }

    function renderError(msg) {
      box.hidden = false;
      box.classList.add("error");
      box.innerHTML = "<p class=\"preview-line\">⚠️ " + escapeHtml(msg) + "</p>";
    }

    function renderResult(data) {
      box.hidden = false;
      box.classList.remove("error");
      let html = "";
      if (data.explanation_pl) {
        html += "<p class=\"preview-line\">" + escapeHtml(data.explanation_pl) + "</p>";
      }
      (data.lines || []).forEach((line) => {
        const cls = line.emphasis ? "preview-line emphasis" : "preview-line";
        const unit = line.unit ? " " + escapeHtml(line.unit) : "";
        html += "<p class=\"" + cls + "\">" + escapeHtml(line.label) + ": "
              + escapeHtml(line.value) + unit + "</p>";
      });
      if (data.table_urls && data.table_urls.nbp) {
        html += "<p class=\"preview-line\"><a href=\"" + escapeHtml(data.table_urls.nbp)
              + "\" target=\"_blank\" rel=\"noopener\">tabela NBP ↗</a></p>";
      }
      box.innerHTML = html;
    }

    function refresh() {
      const { params, ready } = collect();
      if (!ready) {
        box.hidden = true;
        return;
      }
      if (controller) controller.abort();
      controller = new AbortController();
      fetch(config.url + "?" + params.toString(), { signal: controller.signal })
        .then((r) => r.json())
        .then((data) => {
          if (data.ok) renderResult(data);
          else renderError(data.error || "Nie udało się policzyć podglądu.");
        })
        .catch((err) => {
          if (err.name !== "AbortError") renderError("Podgląd chwilowo niedostępny.");
        });
    }

    function schedule() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(refresh, 400);
    }

    form.addEventListener("input", schedule);
    form.addEventListener("change", schedule);
  }

  // Krok 18: grupy nawigacji (Portfel/Podatki/Dane) — `<details class="nav-group">`
  // z base.html rozwija się natywnie w miejscu bez JS (pełny fallback). Tutaj
  // dokładamy: (1) `.js-nav` na `<nav>`, żeby CSS przełączył grupy na pływające
  // menu zamiast rozpychania paska, (2) tylko jedna grupa otwarta naraz, (3)
  // klik poza zamyka wszystkie, (4) Escape zamyka. Enter/Space na `<summary>`
  // działają za darmo — przeglądarka wysyła dla nich `click`, ten sam listener
  // je łapie, bez osobnego handlera klawiatury (wzorzec inny niż initRowToggles,
  // bo `<details>` ma już wbudowaną semantykę klawiaturową).
  function initNavGroups() {
    const nav = document.querySelector(".nav");
    if (!nav) return;
    const groups = Array.from(nav.querySelectorAll(".nav-group"));
    if (!groups.length) return;
    nav.classList.add("js-nav");

    function closeAll(except) {
      groups.forEach((g) => { if (g !== except) g.removeAttribute("open"); });
    }

    groups.forEach((g) => {
      const summary = g.querySelector(":scope > summary");
      if (!summary) return;
      summary.addEventListener("click", (ev) => {
        ev.preventDefault();
        const wasOpen = g.hasAttribute("open");
        closeAll(g);
        if (wasOpen) g.removeAttribute("open");
        else g.setAttribute("open", "");
      });
    });

    document.addEventListener("click", (ev) => {
      if (!ev.target.closest(".nav-group")) closeAll(null);
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") closeAll(null);
    });
  }

  function currentCurrency() {
    return document.documentElement.dataset.currency === "eur" ? "eur" : "pln";
  }

  function initValueChart(canvasId, points) {
    // Krok 25: krzywa wartości portfela vs kontrfaktyczny benchmark OMXH25 na
    // tym samym wykresie — `points`: [{date, value_pln, value_eur, benchmark_pln,
    // benchmark_eur}]. Krok 28.1: obie waluty już policzone server-side
    // (Tier A, docs/PLAN_KROK_28_ux_mobile.md §0) — wykres słucha globalnego
    // przełącznika (`nt:currency-change`) i przerysowuje dane bez przeładowania.
    const el = document.getElementById(canvasId);
    if (!el || !window.Chart || !points || !points.length) return;

    const labels = points.map((p) => formatLabel(p.date, "daily"));

    function seriesFor(cur) {
      return {
        value: points.map((p) => p[cur === "eur" ? "value_eur" : "value_pln"]),
        benchmark: points.map((p) => p[cur === "eur" ? "benchmark_eur" : "benchmark_pln"]),
        unit: cur === "eur" ? "EUR" : "PLN",
      };
    }

    let cur = currentCurrency();
    let s = seriesFor(cur);
    const chart = new Chart(el.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Portfel (" + s.unit + ")",
            data: s.value,
            borderColor: cssVar("--series-1"),
            backgroundColor: cssVar("--series-1") + "22",
            fill: true,
            pointRadius: 0,
            tension: 0.15,
            borderWidth: 2,
          },
          {
            label: "OMXH25 (kontrfaktycznie, " + s.unit + ")",
            data: s.benchmark,
            borderColor: cssVar("--series-2"),
            backgroundColor: "transparent",
            fill: false,
            pointRadius: 0,
            tension: 0.15,
            borderWidth: 2,
            borderDash: [4, 3],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: cssVar("--muted") } } },
        scales: {
          x: {
            display: true,
            ticks: { color: cssVar("--muted"), maxRotation: 0, autoSkip: true },
            grid: { display: false },
          },
          y: { ticks: { color: cssVar("--muted") }, grid: { color: cssVar("--grid") } },
        },
      },
    });

    document.addEventListener("nt:currency-change", (ev) => {
      const next = ev.detail && ev.detail.currency;
      if (!next || next === cur) return;
      cur = next;
      s = seriesFor(cur);
      chart.data.datasets[0].data = s.value;
      chart.data.datasets[0].label = "Portfel (" + s.unit + ")";
      chart.data.datasets[1].data = s.benchmark;
      chart.data.datasets[1].label = "OMXH25 (kontrfaktycznie, " + s.unit + ")";
      chart.update();
    });
  }

  function initBucketDonut(canvasId, buckets) {
    // Krok 28.4: donut trzech kubełków portfela (Wolne/Z ograniczeniem/
    // Zablokowane) na pulpicie — te same kolory co kropki .pf-bucket-title
    // w app.css (--good/--series-3/--baseline), tylko ilości > 0 wchodzą
    // do wykresu (Chart.js rysuje pusty krąg dla samych zer).
    const el = document.getElementById(canvasId);
    if (!el || !window.Chart || !buckets) return;
    const nonZero = buckets.filter((b) => b.qty > 0);
    if (!nonZero.length) return;
    const colors = { "Wolne": cssVar("--good"), "Z ograniczeniem": cssVar("--series-3"),
                     "Zablokowane": cssVar("--baseline") };
    new Chart(el.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: nonZero.map((b) => b.label),
        datasets: [{
          data: nonZero.map((b) => b.qty),
          backgroundColor: nonZero.map((b) => colors[b.label] || cssVar("--muted")),
          borderColor: cssVar("--page"), borderWidth: 2,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "65%",
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
      },
    });
  }

  function initDividendBarChart(canvasId, yearly) {
    // Krok 28.4: dywidendy rok po roku — brutto vs netto (po podatku u źródła),
    // `yearly`: [{year, gross_pln, net_pln}], już zagregowane server-side.
    const el = document.getElementById(canvasId);
    if (!el || !window.Chart || !yearly || !yearly.length) return;
    new Chart(el.getContext("2d"), {
      type: "bar",
      data: {
        labels: yearly.map((y) => y.year),
        datasets: [
          { label: "Brutto PLN", data: yearly.map((y) => y.gross_pln),
            backgroundColor: cssVar("--series-1") + "55", borderColor: cssVar("--series-1"),
            borderWidth: 1, maxBarThickness: 48 },
          { label: "Netto PLN", data: yearly.map((y) => y.net_pln),
            backgroundColor: cssVar("--series-1"), borderColor: cssVar("--series-1"),
            borderWidth: 1, maxBarThickness: 48 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: cssVar("--muted") } } },
        scales: {
          x: { ticks: { color: cssVar("--muted") }, grid: { display: false } },
          y: { ticks: { color: cssVar("--muted") }, grid: { color: cssVar("--grid") } },
        },
      },
    });
  }

  function initWaterfallChart(canvasId, w) {
    // Krok 28.4: waterfall Poz. C PIT-38 — przychód/koszt/dochód/strata
    // odliczona (informacyjna)/podatek/na rękę. Chart.js `bar` obsługuje
    // "pływające" słupki natywnie przez pary [y0, y1] w `data` (bez sztuczek
    // z przezroczystym datasetem-offsetem). "Strata odliczona" NIE wchodzi
    // do łańcucha przychód-koszt-podatek-na rękę (patrz komentarz w web.py) —
    // rysowana jako osobny, wizualnie odróżniony słupek.
    const el = document.getElementById(canvasId);
    if (!el || !window.Chart || !w) return;
    const net = w.income - w.tax;
    const labels = ["Przychód", "Koszt", "Dochód", "Strata odliczona", "Podatek", "Na rękę"];
    const data = [
      [0, w.revenue], [w.income, w.revenue], [0, w.income],
      [w.income - w.loss_used, w.income], [net, w.income], [0, net],
    ];
    const good = cssVar("--good"), bad = cssVar("--bad"), s1 = cssVar("--series-1"),
          s3 = cssVar("--series-3");
    const colors = [good, bad, s1, s3, bad, s1];
    new Chart(el.getContext("2d"), {
      type: "bar",
      data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: cssVar("--muted") }, grid: { display: false } },
          y: { ticks: { color: cssVar("--muted") }, grid: { color: cssVar("--grid") } },
        },
      },
    });
  }

  // Krok 28.1 (docs/PLAN_KROK_28_ux_mobile.md): globalny przełącznik waluty.
  // Ustawia `<html data-currency>`, zapamiętuje w localStorage (wzorzec
  // `nt.chart.range` powyżej) i rozgłasza `nt:currency-change`, żeby wykresy
  // (initValueChart) mogły przerysować dane bez przeładowania strony. Sam
  // toggle tekstu w DOM (cur_block() w _macros.html) działa czystym CSS —
  // patrz `html[data-currency]` w app.css — event jest potrzebny tylko tam,
  // gdzie CSS nie wystarcza (canvas).
  function initCurrencyToggle(buttonId) {
    const btn = document.getElementById(buttonId);
    if (!btn) return;
    const storageKey = "nt.currency";

    btn.addEventListener("click", () => {
      const next = currentCurrency() === "eur" ? "pln" : "eur";
      document.documentElement.dataset.currency = next;
      if (window.localStorage) {
        try { localStorage.setItem(storageKey, next); } catch (e) {}
      }
      document.dispatchEvent(new CustomEvent("nt:currency-change", { detail: { currency: next } }));
    });
  }

  return {
    initPriceChart, initRowToggles, initFormPreview, initNavGroups, initValueChart,
    initCurrencyToggle, initBucketDonut, initDividendBarChart, initWaterfallChart,
  };
})();
