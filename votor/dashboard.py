from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Direction Dashboard", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from dotenv import load_dotenv
load_dotenv()


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/api/summary")
def api_summary():
    from direction.analytics import get_summary
    return get_summary()


@app.get("/api/queries")
def api_queries(limit: int = 20):
    from direction.analytics import get_recent_queries
    return get_recent_queries(limit=limit)


@app.get("/api/daily")
def api_daily(days: int = 30):
    from direction.analytics import get_daily_stats
    return get_daily_stats(days=days)


@app.get("/api/files")
def api_files(limit: int = 10):
    from direction.analytics import get_top_files
    return get_top_files(limit=limit)


@app.get("/api/models")
def api_models():
    from direction.analytics import get_model_breakdown
    return get_model_breakdown()


@app.get("/api/savings")
def api_savings(days: int = 30):
    from direction.analytics import get_token_savings_trend
    return get_token_savings_trend(days=days)


@app.get("/api/db-stats")
def api_db_stats():
    from direction.db import get_stats
    return get_stats()


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Direction</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Syne:wght@400;600;800&display=swap');

  :root {
    --bg:       #0a0a0f;
    --surface:  #111118;
    --border:   #1e1e2e;
    --accent:   #00ff9d;
    --accent2:  #7c6af7;
    --warn:     #ff6b35;
    --text:     #e2e2f0;
    --muted:    #5a5a7a;
    --card-bg:  #13131f;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', monospace;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* Scanline overlay */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,255,157,0.015) 2px,
      rgba(0,255,157,0.015) 4px
    );
    pointer-events: none;
    z-index: 9999;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1.5rem 2.5rem;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 100;
  }

  .logo {
    font-family: 'Syne', sans-serif;
    font-weight: 800;
    font-size: 1.4rem;
    letter-spacing: -0.02em;
    color: var(--accent);
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }

  .logo-dot {
    width: 8px; height: 8px;
    background: var(--accent);
    border-radius: 50%;
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.8); }
  }

  .header-meta {
    font-size: 0.7rem;
    color: var(--muted);
    text-align: right;
    line-height: 1.6;
  }

  .header-meta span { color: var(--accent); }

  main {
    max-width: 1400px;
    margin: 0 auto;
    padding: 2rem 2.5rem;
    display: flex;
    flex-direction: column;
    gap: 2rem;
  }

  /* Stat cards row */
  .stat-grid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 1rem;
  }

  .stat-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1.2rem 1.4rem;
    position: relative;
    overflow: hidden;
    transition: border-color 0.2s;
  }

  .stat-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--accent);
    opacity: 0.6;
  }

  .stat-card:nth-child(2)::before { background: var(--accent2); }
  .stat-card:nth-child(3)::before { background: var(--warn); }
  .stat-card:nth-child(4)::before { background: #00cfff; }
  .stat-card:nth-child(5)::before { background: #ffcc00; }
  .stat-card:nth-child(6)::before { background: #ff6b9d; }

  .stat-card:hover { border-color: var(--accent); }

  .stat-label {
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--muted);
    margin-bottom: 0.6rem;
  }

  .stat-value {
    font-family: 'Syne', sans-serif;
    font-size: 1.8rem;
    font-weight: 800;
    color: var(--text);
    line-height: 1;
    margin-bottom: 0.3rem;
  }

  .stat-sub {
    font-size: 0.65rem;
    color: var(--muted);
  }

  /* Chart grid */
  .chart-grid {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 1rem;
  }

  .chart-grid-3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 1rem;
  }

  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1.5rem;
  }

  .card-title {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--muted);
    margin-bottom: 1.2rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .card-title::before {
    content: '//';
    color: var(--accent);
    font-weight: 700;
  }

  canvas { max-height: 220px; }

  /* Query table */
  .query-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.72rem;
  }

  .query-table th {
    text-align: left;
    padding: 0.5rem 0.8rem;
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }

  .query-table td {
    padding: 0.6rem 0.8rem;
    border-bottom: 1px solid rgba(30,30,46,0.6);
    vertical-align: middle;
  }

  .query-table tr:hover td { background: rgba(0,255,157,0.03); }

  .query-text {
    max-width: 300px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text);
  }

  .badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 2px;
    font-size: 0.6rem;
    font-weight: 500;
    letter-spacing: 0.05em;
  }

  .badge-mini { background: rgba(0,255,157,0.1); color: var(--accent); }
  .badge-full { background: rgba(124,106,247,0.15); color: var(--accent2); }

  .score-bar {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .score-track {
    width: 60px; height: 3px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
  }

  .score-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    transition: width 0.5s ease;
  }

  /* File list */
  .file-list { display: flex; flex-direction: column; gap: 0.5rem; }

  .file-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0;
    border-bottom: 1px solid rgba(30,30,46,0.5);
    font-size: 0.72rem;
    gap: 0.8rem;
  }

  .file-name {
    color: var(--accent);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    font-size: 0.68rem;
  }

  .file-count {
    color: var(--muted);
    white-space: nowrap;
    font-size: 0.65rem;
  }

  .file-bar-wrap {
    width: 80px; height: 3px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .file-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent2), var(--accent));
    border-radius: 2px;
  }

  /* Loading */
  .loading {
    color: var(--muted);
    font-size: 0.7rem;
    text-align: center;
    padding: 2rem;
    animation: blink 1s infinite;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  /* Refresh button */
  .refresh-btn {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    padding: 0.3rem 0.8rem;
    border-radius: 2px;
    cursor: pointer;
    transition: all 0.2s;
    letter-spacing: 0.08em;
  }

  .refresh-btn:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .empty { color: var(--muted); font-size: 0.72rem; text-align: center; padding: 2rem; }

  @media (max-width: 1100px) {
    .stat-grid { grid-template-columns: repeat(3, 1fr); }
    .chart-grid { grid-template-columns: 1fr; }
    .chart-grid-3 { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-dot"></div>
    direction
  </div>
  <div class="header-meta">
    <div>project analytics dashboard</div>
    <div>last refresh: <span id="last-refresh">—</span></div>
  </div>
  <button class="refresh-btn" onclick="loadAll()">↻ refresh</button>
</header>

<main>

  <!-- Stat Cards -->
  <div class="stat-grid" id="stat-grid">
    <div class="loading" style="grid-column:1/-1">loading...</div>
  </div>

  <!-- Charts row 1 -->
  <div class="chart-grid">
    <div class="card">
      <div class="card-title">daily token usage</div>
      <canvas id="tokenChart"></canvas>
    </div>
    <div class="card">
      <div class="card-title">model distribution</div>
      <canvas id="modelChart"></canvas>
    </div>
  </div>

  <!-- Charts row 2 -->
  <div class="chart-grid">
    <div class="card">
      <div class="card-title">token savings vs full context</div>
      <canvas id="savingsChart"></canvas>
    </div>
    <div class="card">
      <div class="card-title">top accessed files</div>
      <div class="file-list" id="file-list">
        <div class="loading">loading...</div>
      </div>
    </div>
  </div>

  <!-- Recent queries -->
  <div class="card">
    <div class="card-title">recent queries</div>
    <div id="query-table-wrap">
      <div class="loading">loading...</div>
    </div>
  </div>

</main>

<script>
const API = '';
let charts = {};

const CHART_DEFAULTS = {
  color: '#e2e2f0',
  plugins: {
    legend: { labels: { color: '#5a5a7a', font: { family: 'JetBrains Mono', size: 11 } } }
  },
  scales: {
    x: { ticks: { color: '#5a5a7a', font: { family: 'JetBrains Mono', size: 10 } }, grid: { color: '#1e1e2e' } },
    y: { ticks: { color: '#5a5a7a', font: { family: 'JetBrains Mono', size: 10 } }, grid: { color: '#1e1e2e' } }
  }
};
  color: '#e2e2f0',
  plugins: {
    legend: { labels: { color: '#5a5a7a', font: { family: 'JetBrains Mono', size: 11 } } }
  },
  scales: {
    x: { ticks: { color: '#5a5a7a', font: { family: 'JetBrains Mono', size: 10 } }, grid: { color: '#1e1e2e' } },
    y: { ticks: { color: '#5a5a7a', font: { family: 'JetBrains Mono', size: 10 } }, grid: { color: '#1e1e2e' } }
  }
};

function fmt(n, dec=0) {
  if (n === null || n === undefined) return '—';
  if (n >= 1_000_000) return (n/1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n/1_000).toFixed(1) + 'K';
  return Number(n).toFixed(dec);
}

function fmtCost(n) {
  if (!n) return '$0.0000';
  return '$' + Number(n).toFixed(4);
}

function fmtTime(n) {
  if (!n) return '—';
  return Number(n).toFixed(2) + 's';
}

async function fetchJSON(path) {
  const r = await fetch(API + path);
  return r.json();
}

async function loadSummary() {
  const s = await fetchJSON('/api/summary');
  const dbStats = await fetchJSON('/api/db-stats');

  const cards = [
    { label: 'total queries',     value: fmt(s.total_queries),          sub: 'all time' },
    { label: 'tokens used',       value: fmt(s.total_tokens),           sub: 'input + output' },
    { label: 'total cost',        value: fmtCost(s.total_cost),         sub: 'USD' },
    { label: 'avg response',      value: fmtTime(s.avg_response_time),  sub: 'per query' },
    { label: 'indexed chunks',    value: fmt(dbStats.total_chunks),     sub: `${fmt(dbStats.total_files)} files` },
    { label: 'avg retrieval',     value: s.avg_retrieval_score ? (s.avg_retrieval_score * 100).toFixed(1) + '%' : '—', sub: 'relevance score' },
  ];

  document.getElementById('stat-grid').innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="stat-label">${c.label}</div>
      <div class="stat-value">${c.value}</div>
      <div class="stat-sub">${c.sub}</div>
    </div>
  `).join('');
}

async function loadTokenChart() {
  const data = await fetchJSON('/api/daily?days=30');
  if (charts.token) charts.token.destroy();

  const ctx = document.getElementById('tokenChart').getContext('2d');
  charts.token = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.map(d => d.date.slice(5)),
      datasets: [
        {
          label: 'tokens used',
          data: data.map(d => d.total_tokens),
          backgroundColor: 'rgba(0,255,157,0.2)',
          borderColor: '#00ff9d',
          borderWidth: 1,
        },
        {
          label: 'tokens saved',
          data: data.map(d => d.total_tokens_saved),
          backgroundColor: 'rgba(124,106,247,0.2)',
          borderColor: '#7c6af7',
          borderWidth: 1,
        }
      ]
    },
    options: {
      ...CHART_DEFAULTS,
      responsive: true,
      plugins: { ...CHART_DEFAULTS.plugins },
      scales: CHART_DEFAULTS.scales
    }
  });
}

async function loadModelChart() {
  const data = await fetchJSON('/api/models');
  if (charts.model) charts.model.destroy();

  if (!data.length) {
    document.getElementById('modelChart').parentElement.innerHTML += '<div class="empty">no data yet</div>';
    return;
  }

  const ctx = document.getElementById('modelChart').getContext('2d');
  charts.model = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: data.map(d => d.model),
      datasets: [{
        data: data.map(d => d.total_queries),
        backgroundColor: ['rgba(0,255,157,0.7)', 'rgba(124,106,247,0.7)', 'rgba(255,107,53,0.7)'],
        borderColor: '#0a0a0f',
        borderWidth: 3
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#5a5a7a', font: { family: 'JetBrains Mono', size: 10 }, padding: 16 } }
      }
    }
  });
}

async function loadSavingsChart() {
  const data = await fetchJSON('/api/savings?days=30');
  if (charts.savings) charts.savings.destroy();

  const ctx = document.getElementById('savingsChart').getContext('2d');
  charts.savings = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map(d => d.date.slice(5)),
      datasets: [{
        label: 'savings %',
        data: data.map(d => d.savings_pct),
        borderColor: '#00ff9d',
        backgroundColor: 'rgba(0,255,157,0.05)',
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: '#00ff9d',
        tension: 0.4,
        fill: true
      }]
    },
    options: {
      ...CHART_DEFAULTS,
      responsive: true,
      plugins: { ...CHART_DEFAULTS.plugins },
      scales: {
        ...CHART_DEFAULTS.scales,
        y: {
          ...CHART_DEFAULTS.scales.y,
          min: 0, max: 100,
          ticks: { ...CHART_DEFAULTS.scales.y.ticks, callback: v => v + '%' }
        }
      }
    }
  });
}

async function loadFiles() {
  const data = await fetchJSON('/api/files?limit=10');
  const el = document.getElementById('file-list');

  if (!data.length) {
    el.innerHTML = '<div class="empty">no file access data yet</div>';
    return;
  }

  const max = data[0].access_count;
  el.innerHTML = data.map(f => `
    <div class="file-row">
      <div class="file-name">${f.file_path}</div>
      <div class="file-bar-wrap">
        <div class="file-bar-fill" style="width:${(f.access_count/max*100).toFixed(0)}%"></div>
      </div>
      <div class="file-count">${f.access_count}x</div>
    </div>
  `).join('');
}

async function loadQueries() {
  const data = await fetchJSON('/api/queries?limit=20');
  const wrap = document.getElementById('query-table-wrap');

  if (!data.length) {
    wrap.innerHTML = '<div class="empty">no queries yet — run `direction query` to get started</div>';
    return;
  }

  wrap.innerHTML = `
    <table class="query-table">
      <thead>
        <tr>
          <th>question</th>
          <th>model</th>
          <th>tokens</th>
          <th>cost</th>
          <th>time</th>
          <th>retrieval</th>
          <th>saved</th>
          <th>timestamp</th>
        </tr>
      </thead>
      <tbody>
        ${data.map(q => `
          <tr>
            <td><div class="query-text" title="${q.question}">${q.question}</div></td>
            <td>
              <span class="badge ${q.model.includes('mini') ? 'badge-mini' : 'badge-full'}">
                ${q.model.includes('mini') ? 'mini' : '4o'}
              </span>
            </td>
            <td>${fmt(q.total_tokens)}</td>
            <td>${fmtCost(q.cost)}</td>
            <td>${fmtTime(q.response_time)}</td>
            <td>
              <div class="score-bar">
                <div class="score-track">
                  <div class="score-fill" style="width:${(q.retrieval_score*100).toFixed(0)}%"></div>
                </div>
                <span style="font-size:0.65rem;color:var(--muted)">${(q.retrieval_score*100).toFixed(0)}%</span>
              </div>
            </td>
            <td style="color:var(--accent)">${fmt(q.tokens_saved)}</td>
            <td style="color:var(--muted);font-size:0.65rem">${q.timestamp.slice(0,16).replace('T',' ')}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function loadAll() {
  document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();
  await Promise.all([
    loadSummary(),
    loadTokenChart(),
    loadModelChart(),
    loadSavingsChart(),
    loadFiles(),
    loadQueries()
  ]);
}

// Auto-refresh every 30 seconds
loadAll();
setInterval(loadAll, 30_000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)