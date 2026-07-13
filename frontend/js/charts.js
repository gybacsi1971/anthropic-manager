/* Anthropic Manager — Chart.js segédfüggvények (közös paletta, line/stacked/doughnut) */

const CHART_COLORS = [
  '#cc785c', '#3a6ea5', '#2e7d57', '#c77b1f', '#8e6cb0', '#cf5b78',
  '#4aa3a3', '#7d8a3c', '#b5654b', '#5b7db1', '#9c7a4d', '#6a9a6a',
  '#a35b8e', '#d09a3c', '#5c8fb0', '#8c7b5a',
];

const _charts = {};

function destroyChart(id) {
  if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
}

function _tickFmt(format) {
  return (v) => (format === 'usd' ? fmtUSD(v, 0) : fmtTokens(v));
}

function _tooltipFmt(format) {
  return (ctx) => {
    const v = ctx.parsed.y !== undefined ? ctx.parsed.y : ctx.parsed;
    const label = ctx.dataset.label ? ctx.dataset.label + ': ' : '';
    return label + (format === 'usd' ? fmtUSD(v) : fmtInt(v));
  };
}

// X-tengely dátumcímkék: MINDEN nap látszódjon (nincs autoSkip), függőlegesen, tömör
// "MM-DD" formában. A labels tömb változatlan marad, így a tooltip a teljes dátumot mutatja.
function _dateTicks(labels) {
  return {
    autoSkip: false,
    maxRotation: 90,
    minRotation: 90,
    callback: (val, idx) => {
      const l = labels[idx] || '';
      return /^\d{4}-\d{2}-\d{2}$/.test(l) ? l.slice(5) : l;
    },
  };
}

function renderLineChart(canvasId, labels, series, { format = 'tokens', stacked = false } = {}) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const datasets = series.map((s, i) => ({
    label: s.label,
    data: s.data,
    borderColor: CHART_COLORS[i % CHART_COLORS.length],
    backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + (stacked ? 'cc' : '22'),
    borderWidth: 2,
    pointRadius: labels.length > 40 ? 0 : 2.5,
    pointHoverRadius: 4,
    fill: stacked,
    tension: 0.25,
  }));
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: _tooltipFmt(format) } },
      },
      scales: {
        x: { stacked, grid: { display: false }, ticks: _dateTicks(labels) },
        y: { stacked, beginAtZero: true, ticks: { callback: _tickFmt(format) }, grid: { color: '#eceae3' } },
      },
    },
  });
}

// 6-jegyű hex szín + alfa-bájt (pl. '66' ≈ 40% átlátszóság).
function _alphaHex(hex, alpha) { return hex + alpha; }

// estimatedDays: opcionális bool-tömb (labels hosszú). Ahol true, az adott nap oszlopát
// a modell színének 40%-os változatával rajzoljuk, és "becsült" feliratot teszünk fölé.
function renderStackedBar(canvasId, labels, series, { format = 'tokens', estimatedDays = null } = {}) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const datasets = series.map((s, i) => {
    const base = CHART_COLORS[i % CHART_COLORS.length];
    return {
      label: s.label,
      data: s.data,
      backgroundColor: estimatedDays
        ? labels.map((_, j) => (estimatedDays[j] ? _alphaHex(base, '66') : base))
        : base,
      borderWidth: 0,
      borderRadius: 2,
    };
  });

  // "becsült" felirat a becsült napok oszlopa fölé (csak ha kevés van — különben zsúfolt).
  const totals = labels.map((_, j) => series.reduce((a, s) => a + (s.data[j] || 0), 0));
  const estLabelPlugin = {
    id: 'estLabel',
    afterDatasetsDraw(chart) {
      if (!estimatedDays) return;
      const estCount = estimatedDays.filter(Boolean).length;
      if (estCount === 0 || estCount > 8) return;
      const meta = chart.getDatasetMeta(0);
      const yScale = chart.scales.y;
      const c = chart.ctx;
      c.save();
      c.font = '600 10px Inter, sans-serif';
      c.fillStyle = '#9a6a2f';
      c.textAlign = 'center';
      estimatedDays.forEach((est, j) => {
        if (!est || !totals[j]) return;
        const bar = meta.data[j];
        if (!bar) return;
        c.fillText('becsült', bar.x, yScale.getPixelForValue(totals[j]) - 5);
      });
      c.restore();
    },
  };

  _charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    plugins: estimatedDays ? [estLabelPlugin] : [],
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: estimatedDays ? 14 : 0 } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: _tooltipFmt(format) } } },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: _dateTicks(labels) },
        y: { stacked: true, beginAtZero: true, ticks: { callback: _tickFmt(format) }, grid: { color: '#eceae3' } },
      },
    },
  });
}

function renderDoughnut(canvasId, items, { format = 'tokens' } = {}) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  _charts[canvasId] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: items.map((i) => i.label),
      datasets: [{
        data: items.map((i) => i.value),
        backgroundColor: items.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
        borderWidth: 1, borderColor: '#fff',
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '62%',
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${format === 'usd' ? fmtUSD(ctx.parsed) : fmtInt(ctx.parsed)}` } },
      },
    },
  });
}

function renderLegend(containerId, series) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = series.map((s, i) =>
    `<div class="item"><span class="swatch" style="background:${CHART_COLORS[i % CHART_COLORS.length]}"></span>${escapeHtml(s.label)}</div>`
  ).join('');
}
