class TradingDashboard {
    constructor() {
        this.lastUpdateTime = null;
        this.isUpdating = false;
        this.allPairs = [];
        this.zeroFeeOnly = false;
        this.pairMode = null; // 'top_gainer' | 'top_loser' | null
        this.activePairSymbol = null;
        this.bindEvents();
        this.startDataUpdates();
        this.updateDashboard();
        this.loadFuturesPairs();
        setInterval(() => this.loadFuturesPairs(), 60000);
    }

    bindEvents() {
        document.getElementById('start-bot').addEventListener('click', () => this.startBot());
        document.getElementById('stop-bot').addEventListener('click', () => this.stopBot());
        document.getElementById('close-position').addEventListener('click', () => this.closePosition());
        document.getElementById('delete-trade').addEventListener('click', () => this.deleteLastTrade());
        document.getElementById('reset-balance').addEventListener('click', () => this.resetBalance());
        document.getElementById('toggle-counter-trade').addEventListener('click', () => this.toggleCounterTrade());

        document.querySelectorAll('.leverage-btn').forEach(btn => {
            btn.addEventListener('click', () => this.setLeverage(parseInt(btn.dataset.leverage)));
        });

        document.getElementById('refresh-pairs-btn').addEventListener('click', () => this.loadFuturesPairs());
        document.getElementById('mode-gainer-btn').addEventListener('click', () => this.setPairMode('top_gainer'));
        document.getElementById('mode-loser-btn').addEventListener('click', () => this.setPairMode('top_loser'));
    }

    async setPairMode(mode) {
        // Toggle off if already active
        const newMode = this.pairMode === mode ? null : mode;
        const pairs = this.getFilteredPairs();
        let symbol = '';
        if (newMode === 'top_gainer' && pairs.length > 0) symbol = pairs[0].symbol;
        if (newMode === 'top_loser' && pairs.length > 0) symbol = pairs[pairs.length - 1].symbol;

        try {
            const res = await fetch('/api/set_pair_mode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: newMode, symbol })
            });
            const data = await res.json();
            if (res.ok) {
                this.pairMode = newMode;
                this.activePairSymbol = data.active_symbol || symbol;
                this.updatePairModeButtons();
                this.renderPairs();
                this.updateActivePairBadge();
                const modeName = newMode === 'top_gainer' ? '▲ Выросла #1' : newMode === 'top_loser' ? '▼ Упала #1' : 'Режим сброшен';
                this.showNotification('success', `Торговая пара: ${this.activePairSymbol || 'SOL/USDT'} (${modeName})`);
            }
        } catch (e) {
            this.showNotification('error', 'Ошибка смены режима пары');
        }
    }

    updatePairModeButtons() {
        const gBtn = document.getElementById('mode-gainer-btn');
        const lBtn = document.getElementById('mode-loser-btn');
        if (!gBtn || !lBtn) return;

        if (this.pairMode === 'top_gainer') {
            gBtn.classList.remove('btn-outline-success'); gBtn.classList.add('btn-success');
            lBtn.classList.remove('btn-danger'); lBtn.classList.add('btn-outline-danger');
        } else if (this.pairMode === 'top_loser') {
            lBtn.classList.remove('btn-outline-danger'); lBtn.classList.add('btn-danger');
            gBtn.classList.remove('btn-success'); gBtn.classList.add('btn-outline-success');
        } else {
            gBtn.classList.remove('btn-success'); gBtn.classList.add('btn-outline-success');
            lBtn.classList.remove('btn-danger'); lBtn.classList.add('btn-outline-danger');
        }
    }

    updateActivePairBadge() {
        const badge = document.getElementById('active-pair-badge');
        if (!badge) return;
        if (this.pairMode && this.activePairSymbol) {
            const icon = this.pairMode === 'top_gainer' ? '▲' : '▼';
            badge.textContent = `${icon} ${this.activePairSymbol}`;
            badge.className = `badge ${this.pairMode === 'top_gainer' ? 'bg-success' : 'bg-danger'}`;
        } else {
            badge.textContent = '—';
            badge.className = 'badge bg-secondary';
        }
    }

    getFilteredPairs() {
        if (this.zeroFeeOnly) return this.allPairs.filter(p => p.zero_fee);
        return this.allPairs;
    }

    async loadFuturesPairs() {
        try {
            const res = await fetch('/api/futures_pairs');
            const data = await res.json();
            if (data.success && data.pairs) {
                this.allPairs = data.pairs;
                this.renderPairs();
            }
        } catch (e) {
            console.error('Futures pairs load error:', e);
        }
    }

    toggleZeroFee() {
        this.zeroFeeOnly = !this.zeroFeeOnly;
        const btn = document.getElementById('zero-fee-btn');
        if (this.zeroFeeOnly) {
            btn.classList.remove('btn-outline-success');
            btn.classList.add('btn-success');
        } else {
            btn.classList.remove('btn-success');
            btn.classList.add('btn-outline-success');
        }
        this.renderPairs();
    }

    renderPairs() {
        const tbody = document.getElementById('pairs-tbody');
        const countEl = document.getElementById('pairs-count');
        if (!tbody) return;

        const filtered = this.getFilteredPairs();
        const pairs = this.pairMode === 'top_loser'
            ? [...filtered].sort((a, b) => a.change_pct - b.change_pct)
            : filtered;

        if (countEl) {
            countEl.textContent = `(${pairs.length}${this.zeroFeeOnly ? ' · 0% fee' : ''})`;
        }

        if (pairs.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted py-3">Нет данных</td></tr>`;
            return;
        }

        const gainerSym = this.pairMode === 'top_gainer' ? (pairs[0] ? pairs[0].symbol : null) : null;
        const loserSym = this.pairMode === 'top_loser' ? (pairs[0] ? pairs[0].symbol : null) : null;

        tbody.innerHTML = pairs.map((p, i) => {
            const pct = p.change_pct;
            const pctClass = pct > 0 ? 'text-success' : pct < 0 ? 'text-danger' : 'text-muted';
            const pctStr = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
            const price = p.last_price > 0.001
                ? p.last_price.toFixed(p.last_price >= 1 ? 4 : 8)
                : p.last_price.toExponential(4);
            const feeStr = p.zero_fee
                ? '<span class="badge bg-success" style="font-size:0.7em;">0%</span>'
                : (p.maker_fee !== null ? `${(p.maker_fee * 100).toFixed(4)}%` : '—');

            let rowStyle = '';
            let activeIcon = '';
            if (p.symbol === gainerSym) {
                rowStyle = 'style="background:rgba(34,197,94,0.18);border-left:3px solid #22c55e;"';
                activeIcon = ' <span class="badge bg-success" style="font-size:0.65em;">ACTIVE</span>';
            } else if (p.symbol === loserSym) {
                rowStyle = 'style="background:rgba(239,68,68,0.18);border-left:3px solid #ef4444;"';
                activeIcon = ' <span class="badge bg-danger" style="font-size:0.65em;">ACTIVE</span>';
            }

            return `<tr ${rowStyle}>
                <td class="ps-3 text-muted">${i + 1}</td>
                <td class="fw-bold">${p.symbol}${activeIcon}</td>
                <td class="text-end">${price}</td>
                <td class="text-end ${pctClass} fw-bold">${pctStr}</td>
                <td class="text-end pe-3">${feeStr}</td>
            </tr>`;
        }).join('');
    }

    async setLeverage(leverage) {
        try {
            const response = await fetch('/api/set_leverage', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ leverage })
            });
            const data = await response.json();
            if (response.ok) {
                this.updateLeverageButtons(leverage);
                this.showNotification('success', data.message || `Плечо x${leverage} установлено`);
            } else {
                this.showNotification('error', data.error || 'Ошибка смены плеча');
            }
        } catch (error) {
            this.showNotification('error', 'Ошибка соединения');
        }
    }

    async toggleCounterTrade() {
        try {
            const response = await fetch('/api/toggle_counter_trade', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            if (response.ok) {
                this.updateCounterTradeButton(data.counter_trade_enabled);
                this.showNotification('success', data.message || 'Режим изменён');
            } else {
                this.showNotification('error', data.error || 'Ошибка');
            }
        } catch (error) {
            this.showNotification('error', 'Ошибка соединения');
        }
    }

    updateCounterTradeButton(enabled) {
        const btn = document.getElementById('toggle-counter-trade');
        const lbl = document.getElementById('counter-trade-label');
        if (!btn || !lbl) return;
        if (enabled) {
            btn.style.borderColor = '#a855f7';
            btn.style.color = '#a855f7';
            btn.style.backgroundColor = 'rgba(168,85,247,0.15)';
            lbl.textContent = 'КОНТР';
            btn.title = 'Режим: контр-трейд (против сигнала). Нажать для отключения';
        } else {
            btn.style.borderColor = '#22c55e';
            btn.style.color = '#22c55e';
            btn.style.backgroundColor = 'rgba(34,197,94,0.15)';
            lbl.textContent = 'ОБЫЧНЫЙ';
            btn.title = 'Режим: обычный трейд (по сигналу). Нажать для включения контр-трейда';
        }
    }

    updateLeverageButtons(leverage) {
        document.querySelectorAll('.leverage-btn').forEach(btn => {
            const btnLev = parseInt(btn.dataset.leverage);
            if (btnLev === leverage) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    }

    async startBot() {
        try {
            const response = await fetch('/api/start_bot', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            const data = await response.json();
            this.showNotification(response.ok ? 'success' : 'error', data.message || data.error || '');
        } catch (error) {
            this.showNotification('error', 'Server connection error');
        }
    }

    async stopBot() {
        try {
            const response = await fetch('/api/stop_bot', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            const data = await response.json();
            this.showNotification(response.ok ? 'success' : 'error', data.message || data.error || '');
        } catch (error) {
            this.showNotification('error', 'Server connection error');
        }
    }

    async closePosition() {
        try {
            const response = await fetch('/api/close_position', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            const data = await response.json();
            this.showNotification(response.ok ? 'success' : 'error', data.message || data.error || '');
        } catch (error) {
            this.showNotification('error', 'Server connection error');
        }
    }

    async deleteLastTrade() {
        try {
            const response = await fetch('/api/delete_last_trade', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            const data = await response.json();
            this.showNotification(response.ok ? 'success' : 'error', data.message || data.error || '');
            if (response.ok) this.updateDashboard();
        } catch (error) {
            this.showNotification('error', 'Server connection error');
        }
    }

    async resetBalance() {
        try {
            const response = await fetch('/api/reset_balance', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            const data = await response.json();
            this.showNotification(response.ok ? 'success' : 'error', data.message || data.error || '');
            if (response.ok) this.updateDashboard();
        } catch (error) {
            this.showNotification('error', 'Server connection error');
        }
    }

    async updateDashboard() {
        if (this.isUpdating) return;
        this.isUpdating = true;
        try {
            const response = await fetch('/api/status');
            if (!response.ok) return;
            const data = await response.json();

            // Bot status
            const statusBadge = document.getElementById('bot-status');
            if (data.bot_running) {
                statusBadge.textContent = 'RUNNING';
                statusBadge.className = 'badge bg-success fs-5';
            } else {
                statusBadge.textContent = 'STOPPED';
                statusBadge.className = 'badge bg-danger fs-5';
            }

            // Balance
            document.getElementById('balance').textContent = `$${parseFloat(data.balance).toFixed(2)}`;
            document.getElementById('available').textContent = `$${parseFloat(data.available).toFixed(2)}`;

            // Price
            if (data.current_price)
                document.getElementById('current-price').textContent = `$${parseFloat(data.current_price).toFixed(2)}`;

            // SAR directions
            if (data.sar_directions) this.updateSARDirections(data.sar_directions);

            // Position
            if (data.in_position && data.position) {
                document.getElementById('position-status').textContent = data.position.side.toUpperCase();
                // Передаём take_profit_price из корня ответа в объект позиции
                const posWithTP = Object.assign({}, data.position, {
                    _take_profit_price: data.take_profit_price || null
                });
                this.updatePosition(posWithTP, data.current_price);
            } else {
                document.getElementById('position-status').textContent = 'No Position';
                this.clearPosition();
            }

            // Trades
            if (data.trades) this.updateTrades(data.trades);

            // Leverage buttons
            if (data.leverage) this.updateLeverageButtons(data.leverage);

            // Counter trade button
            if (data.counter_trade_enabled !== undefined) this.updateCounterTradeButton(data.counter_trade_enabled);

            // Pair mode sync from server
            if (data.pair_mode !== undefined && data.pair_mode !== this.pairMode) {
                this.pairMode = data.pair_mode;
                this.activePairSymbol = data.active_symbol || null;
                this.updatePairModeButtons();
                this.updateActivePairBadge();
            }

            this.lastUpdateTime = new Date();
        } catch (error) {
            console.error('Dashboard update error:', error);
        } finally {
            this.isUpdating = false;
        }
    }

    updateSARDirections(directions) {
        if (!directions) return;

        const timeframes = ['1m', '3m', '5m', '15m', '30m'];
        timeframes.forEach(tf => {
            const el = document.getElementById(`sar-${tf}`);
            const container = document.getElementById(`sar-${tf}-container`);
            if (!el) return;
            const dir = directions[tf];
            el.className = 'badge sar-badge';
            if (dir === 'long') {
                el.textContent = 'L';
                el.classList.add('bg-success');
                if (container) { container.classList.remove('sar-short', 'sar-na'); container.classList.add('sar-long'); }
            } else if (dir === 'short') {
                el.textContent = 'S';
                el.classList.add('bg-danger');
                if (container) { container.classList.remove('sar-long', 'sar-na'); container.classList.add('sar-short'); }
            } else {
                el.textContent = '—';
                el.classList.add('bg-secondary');
                if (container) { container.classList.remove('sar-long', 'sar-short'); container.classList.add('sar-na'); }
            }
        });

        // Signal based on 1m only
        const d1m = directions['1m'];
        const signalEl = document.getElementById('signal-status');
        if (signalEl) {
            if (d1m === 'long') {
                signalEl.textContent = 'LONG SIGNAL';
                signalEl.className = 'badge signal-badge bg-success';
            } else if (d1m === 'short') {
                signalEl.textContent = 'SHORT SIGNAL';
                signalEl.className = 'badge signal-badge bg-danger';
            } else {
                signalEl.textContent = 'NO SIGNAL';
                signalEl.className = 'badge bg-secondary signal-badge';
            }
        }
    }

    updatePosition(position, currentPrice) {
        document.getElementById('no-position').classList.add('d-none');
        document.getElementById('current-position').classList.remove('d-none');

        const sideBadge = document.getElementById('pos-side');
        if (sideBadge) {
            sideBadge.textContent = position.side.toUpperCase();
            sideBadge.className = position.side === 'long' ? 'badge bg-success ms-1' : 'badge bg-danger ms-1';
        }

        const colorClass = position.side === 'long' ? 'text-success ms-1' : 'text-danger ms-1';

        const entryEl = document.getElementById('pos-entry');
        if (entryEl) { entryEl.textContent = `$${parseFloat(position.entry_price).toFixed(3)}`; entryEl.className = colorClass; }

        const markEl = document.getElementById('pos-mark');
        const markPrice = position.mark_price || currentPrice;
        if (markEl && markPrice) { markEl.textContent = `$${parseFloat(markPrice).toFixed(3)}`; }

        const sizeEl = document.getElementById('pos-size');
        if (sizeEl) { sizeEl.textContent = `${parseFloat(position.size_base).toFixed(4)} SOL`; sizeEl.className = colorClass; }

        const notionalEl = document.getElementById('pos-notional');
        if (notionalEl) { notionalEl.textContent = `$${parseFloat(position.notional).toFixed(4)}`; notionalEl.className = colorClass; }

        const marginEl = document.getElementById('pos-margin');
        if (marginEl && position.margin) { marginEl.textContent = `$${parseFloat(position.margin).toFixed(4)}`; }

        // P&L — берём с биржи если есть, иначе считаем локально
        const pnlEl = document.getElementById('pos-pnl');
        if (pnlEl) {
            let pnl;
            if (position.unrealized_pnl !== undefined && position.unrealized_pnl !== null) {
                pnl = parseFloat(position.unrealized_pnl);
            } else {
                const ep = parseFloat(position.entry_price);
                const sz = parseFloat(position.size_base);
                const nt = parseFloat(position.notional || ep * sz);
                pnl = position.side === 'long' ? (currentPrice - ep) * sz : (ep - currentPrice) * sz;
                pnl -= Math.abs(nt) * 0.0003;
            }
            pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)} USDT`;
            pnlEl.className = pnl >= 0 ? 'text-success fw-bold ms-1' : 'text-danger fw-bold ms-1';

            // ROI
            const roiEl = document.getElementById('pos-roi');
            if (roiEl && position.margin) {
                const roi = (pnl / parseFloat(position.margin)) * 100;
                roiEl.textContent = `${roi >= 0 ? '+' : ''}${roi.toFixed(2)}%`;
                roiEl.className = roi >= 0 ? 'text-success fw-bold ms-1' : 'text-danger fw-bold ms-1';
            }
        }

        // Цена ликвидации
        const liqEl = document.getElementById('pos-liq');
        if (liqEl && position.liquidation_price) {
            liqEl.textContent = `$${parseFloat(position.liquidation_price).toFixed(3)}`;
        }

        // Плечо
        const levEl = document.getElementById('pos-lev');
        if (levEl && position.leverage) { levEl.textContent = `x${parseFloat(position.leverage).toFixed(0)}`; }

        const timeEl = document.getElementById('pos-time');
        if (timeEl && position.entry_time) {
            const elapsed = Math.floor((Date.now() - new Date(position.entry_time).getTime()) / 1000);
            const h = Math.floor(elapsed / 3600);
            const m = Math.floor((elapsed % 3600) / 60);
            const s = elapsed % 60;
            timeEl.textContent = h > 0
                ? `${h}ч ${String(m).padStart(2,'0')}м ${String(s).padStart(2,'0')}с`
                : `${m}м ${String(s).padStart(2,'0')}с`;
            timeEl.className = colorClass;
        }

        // Тейк-профит
        const tpWrap = document.getElementById('pos-tp-wrap');
        const tpEl = document.getElementById('pos-tp');
        if (tpEl && tpWrap) {
            if (position._take_profit_price) {
                tpWrap.style.display = '';
                tpEl.textContent = `$${parseFloat(position._take_profit_price).toFixed(3)}`;
            } else {
                tpWrap.style.display = 'none';
            }
        }
    }

    clearPosition() {
        document.getElementById('no-position').classList.remove('d-none');
        document.getElementById('current-position').classList.add('d-none');
    }

    updateTrades(trades) {
        const container = document.getElementById('trades-container');
        const countEl = document.getElementById('trade-count');
        if (!container) return;

        if (!trades || trades.length === 0) {
            container.innerHTML = `<div class="text-center text-muted py-4"><i class="fas fa-clock fa-2x mb-3"></i><p>No completed trades</p></div>`;
            if (countEl) countEl.textContent = '';
            return;
        }

        if (countEl) countEl.textContent = `${trades.length} trades`;

        const recentTrades = trades.slice(0, 100);
        const html = recentTrades.map(trade => {
            const pnl = parseFloat(trade.pnl);
            const pnlClass = pnl >= 0 ? 'text-success' : 'text-danger';
            const sideClass = trade.side === 'long' ? 'bg-success' : 'bg-danger';
            const exitTime = trade.exit_time || trade.time;
            const exitDate = exitTime ? new Date(exitTime).toLocaleString() : 'N/A';
            return `
            <div class="list-group-item bg-dark border-secondary mb-2">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <span class="badge ${sideClass}">${trade.side.toUpperCase()}</span>
                        <span class="${pnlClass} fw-bold ms-2">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</span>
                    </div>
                    <small class="text-muted">${exitDate}</small>
                </div>
                <div class="mt-1">
                    <small class="text-muted">Entry: $${trade.entry_price.toFixed(2)} → Exit: $${trade.exit_price.toFixed(2)}</small>
                </div>
            </div>`;
        }).join('');

        container.innerHTML = html;
    }

    showNotification(type, message) {
        const el = document.createElement('div');
        el.className = `alert alert-${type === 'error' ? 'danger' : 'success'} alert-dismissible fade show position-fixed`;
        el.style.cssText = 'top:20px;right:20px;z-index:9999;min-width:300px;';
        el.innerHTML = `${message}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 5000);
    }

    startDataUpdates() {
        setInterval(() => this.updateDashboard(), 3000);
    }
}

document.addEventListener('DOMContentLoaded', () => new TradingDashboard());
