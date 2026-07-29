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

  return { initPriceChart, initRowToggles };
})();
