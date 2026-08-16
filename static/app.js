/**
 * Automated Halal Swing Trading System - Application Logic
 */

let activeSignals = [];
let currentModalTicker = "";

document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    fetchStatus();
    loadSignals();
    loadTrends();
});

// 1. TAB NAVIGATION
function initTabs() {
    const tabs = document.querySelectorAll(".nav-tab");
    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            const target = tab.dataset.tab;
            switchTab(target);
        });
    });
}

function switchTab(tabId) {
    document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

    const activeTabBtn = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
    const activeContent = document.getElementById(`tab-${tabId}`);

    if (activeTabBtn) activeTabBtn.classList.add("active");
    if (activeContent) activeContent.classList.add("active");
}

// 2. STATUS CHECK
async function fetchStatus() {
    try {
        const r = await fetch("/api/status");
        const res = await r.json();

        const tgPill = document.getElementById("telegram-status");
        if (res.telegram_configured) {
            tgPill.innerHTML = '<span class="dot green"></span> Telegram Active';
        } else {
            tgPill.innerHTML = '<span class="dot yellow"></span> Telegram Unconfigured';
        }

        const llmPill = document.getElementById("llm-status");
        if (res.llm_configured) {
            llmPill.innerHTML = `<span class="dot green"></span> AI Ready (${res.llm_provider})`;
        } else {
            llmPill.innerHTML = '<span class="dot yellow"></span> AI Fallback Mode';
        }
    } catch (e) {
        console.error("Status fetch error", e);
    }
}

// 3. LOAD ACTIONABLE BUY / SELL SIGNALS
async function loadSignals() {
    const grid = document.getElementById("signals-cards-grid");
    grid.innerHTML = '<div class="loading">⚡ Scanning market for high-probability swing setups...</div>';

    try {
        const r = await fetch("/api/signals");
        const res = await r.json();
        activeSignals = res.data || [];

        if (!activeSignals || activeSignals.length === 0) {
            grid.innerHTML = '<div class="loading">No active BUY or SELL signals currently triggered. Check back during market hours.</div>';
            return;
        }

        grid.innerHTML = activeSignals.map(sig => {
            const badgeClass = sig.signal_type === "STRONG BUY" ? "strong-buy" : sig.signal_type === "BUY" ? "buy" : "sell";
            return `
                <div class="signal-card">
                    <div>
                        <div class="signal-header">
                            <span class="signal-ticker">${sig.ticker}</span>
                            <span class="signal-badge ${badgeClass}">${sig.signal_type}</span>
                        </div>

                        <div class="price-targets-row">
                            <div class="price-box">
                                <label>Entry</label>
                                <span>$${sig.entry_price.toFixed(2)}</span>
                            </div>
                            <div class="price-box target">
                                <label>Target (TP)</label>
                                <span>$${sig.target_price.toFixed(2)} (+${sig.target_pct}%)</span>
                            </div>
                            <div class="price-box stop">
                                <label>Stop Loss (SL)</label>
                                <span>$${sig.stop_loss.toFixed(2)} (${sig.stop_loss_pct}%)</span>
                            </div>
                        </div>

                        <div class="signal-meta">
                            <span>Horizon: <strong>${sig.horizon_days}</strong></span>
                            <span>Risk / Reward: <strong>1 : ${sig.reward_risk_ratio}</strong></span>
                        </div>

                        <div class="signal-reason">
                            💡 ${sig.reason}
                        </div>
                    </div>

                    <div class="signal-actions">
                        <button class="btn btn-primary btn-sm" style="flex:1;" onclick="sendTelegramSignal('${sig.ticker}')">Alert Telegram 📱</button>
                        <button class="btn btn-secondary btn-sm" onclick="openBriefModal('${sig.ticker}')">AI Brief 🤖</button>
                    </div>
                </div>
            `;
        }).join("");

    } catch (e) {
        grid.innerHTML = `<div class="loading">Error loading signals: ${e}</div>`;
    }
}

// 4. STRATEGY BACKTESTER SIMULATOR
async function runBacktest() {
    const capital = document.getElementById("backtest-capital").value;
    const period = document.getElementById("backtest-period").value;

    const statsGrid = document.getElementById("backtest-stats-grid");
    const tbody = document.getElementById("backtest-trade-log-body");

    tbody.innerHTML = '<tr><td colspan="8" class="loading">⌛ Running historical backtest strategy simulation...</td></tr>';
    statsGrid.style.display = "none";

    try {
        const r = await fetch("/api/backtest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ period: period, capital: parseFloat(capital) })
        });
        const res = await r.json();

        if (res.error) {
            tbody.innerHTML = `<tr><td colspan="8" class="loading">Error: ${res.error}</td></tr>`;
            return;
        }

        const s = res.summary || {};
        document.getElementById("bt-final-val").innerText = `$${s.final_capital.toLocaleString()}`;
        document.getElementById("bt-net-profit").innerText = `Net Profit: $${s.net_profit_usd.toLocaleString()} (${s.net_profit_pct > 0 ? '+' : ''}${s.net_profit_pct}%)`;
        document.getElementById("bt-win-rate").innerText = `${s.win_rate_pct}%`;
        document.getElementById("bt-trade-counts").innerText = `${s.wins} Wins / ${s.total_trades} Total Trades`;
        document.getElementById("bt-profit-factor").innerText = `${s.profit_factor}`;
        document.getElementById("bt-max-drawdown").innerText = `${s.max_drawdown_pct}%`;

        statsGrid.style.display = "grid";

        const trades = res.trades || [];
        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="loading">No trades executed during backtest period.</td></tr>';
            return;
        }

        tbody.innerHTML = trades.slice().reverse().map(tr => `
            <tr>
                <td><strong>${tr.ticker}</strong></td>
                <td>${tr.entry_date}</td>
                <td>${tr.exit_date}</td>
                <td>$${tr.entry_price.toFixed(2)}</td>
                <td>$${tr.exit_price.toFixed(2)}</td>
                <td style="color:${tr.pnl_usd >= 0 ? 'var(--accent-emerald)' : 'var(--accent-rose)'}; font-weight:700;">
                    ${tr.pnl_usd >= 0 ? '+' : ''}$${tr.pnl_usd.toFixed(2)}
                </td>
                <td style="color:${tr.pnl_pct >= 0 ? 'var(--accent-emerald)' : 'var(--accent-rose)'}; font-weight:700;">
                    ${tr.pnl_pct >= 0 ? '+' : ''}${tr.pnl_pct}%
                </td>
                <td>
                    <span class="${tr.result === 'WIN' ? 'badge-win' : 'badge-loss'}">${tr.result}</span>
                </td>
            </tr>
        `).join("");

    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" class="loading">Failed to execute backtest: ${e}</td></tr>`;
    }
}

// 5. GLOBAL SECTOR TRENDS
async function loadTrends() {
    try {
        const r = await fetch("/api/trends");
        const res = await r.json();
        const trends = res.data || [];

        const container = document.getElementById("trends-cards-container");
        if (!trends || trends.length === 0) {
            container.innerHTML = '<div class="loading">No trends data available.</div>';
            return;
        }

        container.innerHTML = trends.map(tr => `
            <div class="trend-card">
                <h3>🔥 ${tr.title}</h3>
                <p>${tr.thesis}</p>
                <div class="ticker-tags">
                    ${tr.halal_tickers.map(t => `<span class="ticker-tag" onclick="openBriefModal('${t}')">${t}</span>`).join("")}
                </div>
            </div>
        `).join("");
    } catch (e) {
        console.error("Trends load error", e);
    }
}

// 6. TELEGRAM ALERT DISPATCHER
async function sendTelegramSignal(ticker) {
    try {
        const r = await fetch("/api/telegram/signal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker: ticker })
        });
        const res = await r.json();
        alert(res.message || `Signal alert for ${ticker} dispatched to Telegram!`);
    } catch (e) {
        alert("Failed to send Telegram alert: " + e);
    }
}

// 7. AI BRIEF MODAL
async function openBriefModal(ticker) {
    currentModalTicker = ticker;
    document.getElementById("modal-ticker-title").innerText = `AI Swing Analysis — ${ticker}`;
    const modalBody = document.getElementById("brief-modal-body");
    modalBody.innerHTML = '<div class="loading">⚡ Generating AI swing trade brief...</div>';

    document.getElementById("brief-modal").classList.add("active");

    try {
        const r = await fetch(`/api/brief/${ticker}`);
        const res = await r.json();

        let html = res.content_markdown || "No brief content available.";
        html = html
            .replace(/### (.*?)\n/g, '<h3>$1</h3>')
            .replace(/#### (.*?)\n/g, '<h4>$1</h4>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/- (.*?)\n/g, '<li>$1</li>');

        modalBody.innerHTML = html;
    } catch (e) {
        modalBody.innerHTML = `<div class="loading">Error loading brief: ${e}</div>`;
    }
}

function closeBriefModal() {
    document.getElementById("brief-modal").classList.remove("active");
}

function sendBriefToTelegram() {
    if (currentModalTicker) {
        sendTelegramSignal(currentModalTicker);
    }
}
