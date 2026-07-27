/* Nokia Tracker — minimalny JS: wykres kursu na dashboardzie. */
window.NT = (function () {
  function isDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function initPriceChart(canvasId, closes) {
    const el = document.getElementById(canvasId);
    if (!el || !window.Chart || !closes || !closes.length) return;
    new Chart(el.getContext("2d"), {
      type: "line",
      data: {
        labels: closes.map((_, i) => i),
        datasets: [{
          data: closes,
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
          x: { display: false },
          y: { ticks: { color: cssVar("--muted") }, grid: { color: cssVar("--grid") } },
        },
      },
    });
  }

  return { initPriceChart };
})();
