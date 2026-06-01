const COLORS = {
  cn_equity: "#156f52",
  us_equity: "#275d88",
  crypto: "#aa7b19"
};

const DIMENSION_LABELS = {
  valuation: "估值",
  sentiment: "情绪",
  market: "市场",
  macro: "宏观"
};

const MARKET_ORDER = ["cn_equity", "us_equity", "crypto"];
let dashboardMarkets = [];
let selectedHistory = null;
let selectedRange = "10y";
let selectedMarkets = new Set(MARKET_ORDER);

const RANGE_OPTIONS = [
  { key: "3m", label: "3个月", days: 92 },
  { key: "1y", label: "1年", days: 365 },
  { key: "3y", label: "3年", days: 365 * 3 },
  { key: "5y", label: "5年", days: 365 * 5 },
  { key: "10y", label: "10年", days: 365 * 10 }
];

async function loadDashboard() {
  const response = await fetch("/api/dashboard", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toFixed(digits);
}

function sourceLinks(indicator) {
  if (!indicator.source_links || indicator.source_links.length === 0) {
    return `<span>${indicator.source}</span>`;
  }

  const links = indicator.source_links.map((source) => `
    <a class="source-link" href="${source.url}" target="_blank" rel="noopener noreferrer">${source.label}</a>
  `).join("");
  return `<span>${indicator.source}</span><span class="source-links">${links}</span>`;
}

function explanationList(indicator) {
  const explanation = indicator.explanation || {};
  return [
    explanation.current_value,
    explanation.calculation,
    explanation.meaning,
    explanation.current_state
  ].filter(Boolean).map((item) => `<li>${item}</li>`).join("");
}

function marketPosition(market) {
  return {
    label: market.position_label || "未分区",
    range: market.position_range_label || "--",
    meaning: market.meaning || "暂无分区含义",
    action: market.action_advice || "暂无操作建议"
  };
}

function renderMarketCards(markets) {
  const root = document.querySelector("#marketCards");
  root.innerHTML = markets.map((market) => {
    const accent = COLORS[market.market];
    const position = marketPosition(market);
    const staleText = market.stale_indicators.length
      ? `<span class="stale">${market.stale_indicators.length} 个指标沿用旧值</span>`
      : `<span>数据完整</span>`;
    const bars = Object.entries(market.dimension_scores).map(([key, score]) => `
      <div class="bar-row">
        <span>${DIMENSION_LABELS[key]}</span>
        <div class="bar-track"><div class="bar-fill" style="--value:${score}%;--accent:${accent}"></div></div>
        <strong>${formatNumber(score, 0)}</strong>
      </div>
    `).join("");
    const body = market.available === false
      ? `<div class="unavailable-card">${market.unavailable_reason}</div>`
      : `
        <div class="score-stack">
          <div class="score-box">
            <div class="score-label">短周期位置</div>
            <div class="score-value">${formatNumber(market.short_score, 0)}</div>
          </div>
          <div class="score-box">
            <div class="score-label">长周期位置</div>
            <div class="score-value">${formatNumber(market.long_score, 0)}</div>
          </div>
        </div>
        <div class="dimension-bars">${bars}</div>
      `;

    return `
      <article class="market-card" style="--accent:${accent}">
        <div class="market-top">
          <div>
            <div class="market-name">${market.label}</div>
            <p class="eyebrow">${market.market}</p>
          </div>
          <span class="pill">${position.label}</span>
        </div>
        ${body}
        <div class="position-note">
          <strong>${position.range} · ${position.meaning}</strong>
          <span>${position.action}</span>
        </div>
        <div class="meta" style="justify-content:flex-start;margin-top:14px">
          <span>${market.as_of_date}</span>
          ${staleText}
        </div>
      </article>
    `;
  }).join("");
}

function renderDataNotice(payload) {
  const root = document.querySelector("#dataNotice");
  if (!payload.data_notes || payload.data_notes.length === 0) {
    root.innerHTML = "";
    return;
  }

  const unavailableMarkets = (payload.markets || []).filter((market) => market.available === false);
  const failureNotes = payload.data_notes.filter((note) =>
    note.includes("获取失败") ||
    note.includes(" 失败：") ||
    note.includes("未返回有效") ||
    note.includes("未成功返回")
  );
  const hasFailure = unavailableMarkets.length > 0 || failureNotes.length > 0;
  const mode = hasFailure
    ? "部分实时数据缺失"
    : (payload.data_mode === "real" ? "真实数据模式" : (payload.data_mode === "hybrid" ? "混合数据" : "样例数据"));
  root.className = `data-notice ${hasFailure ? "warning" : "info"}`;
  root.innerHTML = `
    <div class="notice-title">${mode}</div>
    <div class="notice-copy">${payload.data_notes.join(" ")}</div>
  `;
}

function renderLegend(markets) {
  document.querySelector("#chartLegend").innerHTML = markets.map((market) => `
    <span class="legend-item"><span class="legend-dot" style="--accent:${COLORS[market.market]}"></span>${market.label}</span>
  `).join("");
}

function selectedChartMarkets(markets) {
  return markets.filter((market) => selectedMarkets.has(market.market));
}

function renderMarketFilter(markets) {
  const root = document.querySelector("#marketFilter");
  const allSelected = markets.length > 0 && markets.every((market) => selectedMarkets.has(market.market));
  const marketButtons = markets.map((market) => `
    <button
      class="market-toggle ${selectedMarkets.has(market.market) ? "active" : ""}"
      data-market="${market.market}"
      type="button"
      style="--accent:${COLORS[market.market]}"
      aria-pressed="${selectedMarkets.has(market.market)}"
    >
      <span class="legend-dot"></span>${market.label}
    </button>
  `).join("");

  root.innerHTML = `
    <button class="market-toggle all ${allSelected ? "active" : ""}" data-market="all" type="button" aria-pressed="${allSelected}">全部</button>
    ${marketButtons}
  `;

  root.querySelectorAll(".market-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const marketKey = button.dataset.market;
      if (marketKey === "all") {
        selectedMarkets = new Set(MARKET_ORDER);
      } else if (selectedMarkets.has(marketKey)) {
        if (selectedMarkets.size > 1) {
          selectedMarkets.delete(marketKey);
        }
      } else {
        selectedMarkets.add(marketKey);
      }

      selectedHistory = null;
      renderMarketFilter(dashboardMarkets);
      renderLegend(selectedChartMarkets(dashboardMarkets));
      renderChart(dashboardMarkets);
    });
  });
}

function renderRangePicker() {
  document.querySelector("#rangePicker").innerHTML = RANGE_OPTIONS.map((option) => `
    <button class="range-button ${option.key === selectedRange ? "active" : ""}" data-range="${option.key}" type="button">${option.label}</button>
  `).join("");
  document.querySelectorAll(".range-button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedRange = button.dataset.range;
      selectedHistory = null;
      renderRangePicker();
      renderChart(dashboardMarkets);
    });
  });
}

function filteredHistory(market) {
  const option = RANGE_OPTIONS.find((item) => item.key === selectedRange) || RANGE_OPTIONS[RANGE_OPTIONS.length - 1];
  const history = market.history || [];
  if (!history.length) {
    return [];
  }
  const latest = new Date(history[history.length - 1].date);
  const cutoff = new Date(latest);
  cutoff.setDate(cutoff.getDate() - option.days);
  return history.filter((item) => new Date(item.date) >= cutoff);
}

function sampledHistory(history, maxPoints = 220) {
  if (history.length <= maxPoints) {
    return history.map((item, index) => ({ item, sourceIndex: index }));
  }
  const step = (history.length - 1) / (maxPoints - 1);
  return Array.from({ length: maxPoints }, (_, index) => {
    const sourceIndex = Math.round(index * step);
    return { item: history[sourceIndex], sourceIndex };
  });
}

function pointFor(item, index, total, innerW, innerH, pad) {
  return {
    item,
    x: pad.left + (index / Math.max(1, total - 1)) * innerW,
    y: pad.top + innerH - (item.score / 100) * innerH
  };
}

function smoothPath(points) {
  if (!points.length) {
    return "";
  }
  if (points.length === 1) {
    return `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
  }
  const commands = [`M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`];
  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    const midX = (current.x + next.x) / 2;
    commands.push(`C ${midX.toFixed(2)} ${current.y.toFixed(2)}, ${midX.toFixed(2)} ${next.y.toFixed(2)}, ${next.x.toFixed(2)} ${next.y.toFixed(2)}`);
  }
  return commands.join(" ");
}

function renderChart(markets) {
  dashboardMarkets = markets;
  const visibleMarkets = selectedChartMarkets(markets);
  const drawableMarkets = visibleMarkets
    .map((market) => ({ ...market, visibleHistory: filteredHistory(market) }))
    .filter((market) => market.visibleHistory.length);
  if (!drawableMarkets.length) {
    document.querySelector("#scoreChart").innerHTML = `<div class="loading">暂无可绘制的历史数据</div>`;
    return;
  }
  const width = 1000;
  const height = 300;
  const pad = { top: 22, right: 24, bottom: 34, left: 42 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const sampled = new Map(drawableMarkets.map((market) => [market.market, sampledHistory(market.visibleHistory)]));
  const maxPoints = Math.max(...Array.from(sampled.values()).map((items) => items.length));
  const chartSeries = drawableMarkets.map((market) => {
    const entries = sampled.get(market.market);
    const points = entries.map(({ item }, index) => pointFor(item, index, entries.length, innerW, innerH, pad));
    return { market, entries, points };
  });

  const grid = [0, 25, 50, 75, 100].map((tick) => {
    const y = pad.top + innerH - (tick / 100) * innerH;
    return `<g><line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#d8ded6"/><text x="8" y="${y + 4}" fill="#637066" font-size="12">${tick}</text></g>`;
  }).join("");

  const paths = chartSeries.map(({ market, points }) => {
    return `<path d="${smoothPath(points)}" fill="none" stroke="${COLORS[market.market]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
  }).join("");

  document.querySelector("#scoreChart").innerHTML = `
    <svg id="historySvg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <rect width="${width}" height="${height}" fill="#fbf8ef"/>
      ${grid}
      ${paths}
      <line id="hoverLine" class="hover-line" x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" visibility="hidden"/>
      <g id="hoverDots"></g>
      <rect id="hoverCapture" x="${pad.left}" y="${pad.top}" width="${innerW}" height="${innerH}" fill="transparent"/>
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" stroke="#17201a" stroke-width="1.5"/>
    </svg>
    <div id="chartTooltip" class="chart-tooltip" hidden></div>
  `;
  const svg = document.querySelector("#historySvg");
  const tooltip = document.querySelector("#chartTooltip");
  const hoverLine = document.querySelector("#hoverLine");
  const hoverDots = document.querySelector("#hoverDots");
  document.querySelector("#hoverCapture").addEventListener("mousemove", (event) => {
    const rect = svg.getBoundingClientRect();
    const viewX = (event.clientX - rect.left) / rect.width * width;
    const ratio = Math.max(0, Math.min(1, (viewX - pad.left) / innerW));
    const hoverX = pad.left + ratio * innerW;
    hoverLine.setAttribute("x1", hoverX.toFixed(2));
    hoverLine.setAttribute("x2", hoverX.toFixed(2));
    hoverLine.setAttribute("visibility", "visible");

    const rows = chartSeries.map(({ market, points }) => {
      const index = Math.min(points.length - 1, Math.max(0, Math.round(ratio * (points.length - 1))));
      return { market, point: points[index] };
    });
    hoverDots.innerHTML = rows.map(({ market, point }) => `<circle cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="5" fill="${COLORS[market.market]}" stroke="#fbf8ef" stroke-width="2"/>`).join("");
    tooltip.hidden = false;
    tooltip.style.left = `${Math.min(rect.width - 230, Math.max(10, event.clientX - rect.left + 14))}px`;
    tooltip.style.top = `${Math.max(10, event.clientY - rect.top + 14)}px`;
    tooltip.innerHTML = `
      <strong>${rows[0].point.item.date}</strong>
      ${rows.map(({ market, point }) => `<span><i style="background:${COLORS[market.market]}"></i>${market.label}: ${formatNumber(point.item.score, 1)}</span>`).join("")}
    `;
    renderHistoryInspector(rows[0].market, rows[0].point.item);
  });
  document.querySelector("#hoverCapture").addEventListener("mouseleave", () => {
    hoverLine.setAttribute("visibility", "hidden");
    hoverDots.innerHTML = "";
    tooltip.hidden = true;
  });
  const defaultMarket = drawableMarkets[0];
  if (defaultMarket && !selectedHistory) {
    renderHistoryInspector(defaultMarket, defaultMarket.visibleHistory[defaultMarket.visibleHistory.length - 1]);
  }
}

function renderHistoryInspector(market, history) {
  selectedHistory = { market: market.market, date: history.date };
  const dimensions = Object.entries(history.dimension_scores || {}).map(([key, score]) => `
    <div class="history-metric">
      <small>${DIMENSION_LABELS[key]}</small>
      <strong>${formatNumber(score, 1)}</strong>
    </div>
  `).join("");
  const indicators = (history.indicators || []).map((indicator) => `
    <tr>
      <td>${indicator.name}</td>
      <td>${DIMENSION_LABELS[indicator.dimension]}</td>
      <td>${formatNumber(indicator.value, 2)}${indicator.unit === "index" ? "" : indicator.unit}</td>
      <td>${formatNumber(indicator.percentile, 0)}%</td>
      <td>${formatNumber(indicator.score, 1)}</td>
    </tr>
  `).join("");

  document.querySelector("#historyInspector").innerHTML = `
    <div class="history-head" style="--accent:${COLORS[market.market]}">
      <div>
        <p class="eyebrow">History Detail</p>
        <h3>${market.label} · ${history.date}</h3>
      </div>
      <div class="history-score">${formatNumber(history.score, 1)}</div>
    </div>
    <div class="history-dimensions">${dimensions}</div>
    <div class="history-table-wrap">
      <table class="history-table">
        <thead>
          <tr><th>指标</th><th>维度</th><th>当日值</th><th>原始分位</th><th>位置分</th></tr>
        </thead>
        <tbody>${indicators}</tbody>
      </table>
    </div>
  `;
}

function renderDimensions(markets) {
  document.querySelector("#dimensionBreakdown").innerHTML = markets.map((market) => {
    if (market.available === false) {
      return `
        <div class="dimension-market">
          <h3>${market.label}</h3>
          <p class="muted-text">数据不可用，暂未计算四维拆解。</p>
        </div>
      `;
    }
    const rows = Object.entries(market.dimension_scores).map(([key, score]) => `
      <div class="bar-row">
        <span>${DIMENSION_LABELS[key]}</span>
        <div class="bar-track"><div class="bar-fill" style="--value:${score}%;--accent:${COLORS[market.market]}"></div></div>
        <strong>${formatNumber(score, 1)}</strong>
      </div>
    `).join("");
    return `
      <div class="dimension-market">
        <h3>${market.label}</h3>
        <div class="dimension-bars">${rows}</div>
      </div>
    `;
  }).join("");
}

function renderIndicators(markets) {
  document.querySelector("#indicatorTable").innerHTML = markets.map((market) => {
    if (market.available === false) {
      return `
        <section class="indicator-group" style="--accent:${COLORS[market.market]}">
          <div class="indicator-group-head">
            <h3>${market.label}</h3>
            <span>数据不可用</span>
          </div>
          <div class="unavailable-card">${market.unavailable_reason}</div>
        </section>
      `;
    }
    const rows = market.indicators.map((indicator) => `
      <div class="indicator-row">
        <div>
          <strong>${indicator.name}</strong>
          <small>${DIMENSION_LABELS[indicator.dimension]} · 位置分</small>
        </div>
        <div class="indicator-metrics">
          <div>
            <small>当前数值</small>
            <strong>${formatNumber(indicator.value, 2)}${indicator.unit === "index" ? "" : indicator.unit}</strong>
          </div>
          <div>
            <small>原始分位</small>
            <strong>${formatNumber(indicator.percentile, 0)}%</strong>
          </div>
          <div>
            <small>位置得分</small>
            <strong>${formatNumber(indicator.score, 0)}</strong>
          </div>
          <div>
            <small>数据日期</small>
            <strong>${indicator.as_of_date.slice(5)}</strong>
          </div>
        </div>
        <div class="indicator-explain wide">
          <small class="source-cell">来源：${sourceLinks(indicator)}</small>
          <ul>${explanationList(indicator)}</ul>
          <span class="${indicator.used_stale_value ? "stale" : ""}">
            ${indicator.used_stale_value ? "沿用最近有效值" : ""}
          </span>
        </div>
      </div>
    `).join("");

    return `
      <section class="indicator-group" style="--accent:${COLORS[market.market]}">
        <div class="indicator-group-head">
          <h3>${market.label}</h3>
          <span>${market.indicators.length} 个指标</span>
        </div>
        <div class="indicator-group-rows">${rows}</div>
      </section>
    `;
  }).join("");
}

function sortMarkets(markets) {
  return [...markets].sort((a, b) => MARKET_ORDER.indexOf(a.market) - MARKET_ORDER.indexOf(b.market));
}

async function main() {
  try {
    const payload = await loadDashboard();
    const markets = sortMarkets(payload.markets);
    document.querySelector("#generatedAt").textContent = `更新 ${formatDateTime(payload.generated_at)}`;
    renderDataNotice(payload);
    renderMarketCards(markets);
    renderMarketFilter(markets);
    renderRangePicker();
    renderLegend(selectedChartMarkets(markets));
    renderChart(markets);
    renderDimensions(markets);
    renderIndicators(markets);
  } catch (error) {
    document.querySelector("#marketCards").innerHTML = `<div class="panel error">数据加载失败：${error.message}</div>`;
  }
}

main();
