import os
import time
import json
import threading
import random
import urllib.request
from datetime import datetime, timedelta

import math
import ccxt
import pandas as pd
from ta.trend import PSARIndicator
import logging
from market_simulator import MarketSimulator
from signal_sender import SignalSender

def smart_round(price: float) -> float:
    """Round price keeping at least 4 significant figures, never loses precision for tiny prices."""
    if price == 0:
        return 0.0
    magnitude = math.floor(math.log10(abs(price)))  # e.g. -4 for 0.000115
    decimals = max(4, -magnitude + 3)                # at least 4 sig figs after leading zeros
    return round(price, decimals)

# ========== Прямой REST API (MEXC + Binance fallback) ==========
def _rest_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def fetch_ohlcv_mexc(symbol="SOLUSDT", interval="1m", limit=200):
    """OHLCV с MEXC REST API."""
    url = f"https://api.mexc.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    raw = _rest_get(url)
    return [[int(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[4]), float(d[5])] for d in raw]

def fetch_price_mexc(symbol="SOLUSDT"):
    """Текущая цена SOL с MEXC REST API."""
    data = _rest_get(f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol}")
    return float(data["price"])

# ========== Конфигурация ==========
# Ключи MEXC (приоритет) или AscendEx (обратная совместимость)
API_KEY    = os.getenv("MEXC_API_KEY",    os.getenv("ASCENDEX_API_KEY", ""))
API_SECRET = os.getenv("MEXC_SECRET",     os.getenv("ASCENDEX_SECRET",  ""))
RUN_IN_PAPER = os.getenv("RUN_IN_PAPER", "0") == "1"
USE_SIMULATOR = os.getenv("USE_SIMULATOR", "0") == "1"

SYMBOL        = "SOL/USDT:USDT"  # MEXC linear perpetual futures
SYMBOL_SPOT   = "SOL/USDT"       # для публичного OHLCV (не требует ключей)
LEVERAGE = 10  # плечо x10 (может быть изменено через API)
ISOLATED = False  # кросс-маржа
POSITION_PERCENT = 0.50  # 50% от доступного баланса (кросс-маржа)
TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}  # 4 таймфрейма: 1м, 5м, 15м, 30м
MIN_TRADE_SECONDS = 120  # минимальная длительность сделки 2 минуты
MIN_RANDOM_TRADE_SECONDS = 480  # минимальная случайная длительность сделки 8 минут
MAX_RANDOM_TRADE_SECONDS = 780  # максимальная случайная длительность сделки 13 минут
PAUSE_BETWEEN_TRADES = 0  # пауза между сделками убрана
START_BANK = 100.0  # стартовый банк (для бумажной торговли / учета)
DASHBOARD_MAX = 100
ALLOWED_LEVERAGES = [3, 5, 10, 20]

# ========== Глобальные переменные состояния ==========
state = {
    "balance": START_BANK,
    "available": START_BANK,
    "in_position": False,
    "position": None,  # dict: {side, entry_price, size_base, entry_time}
    "last_trade_time": None,
    "last_1m_dir": None,
    "one_min_flip_count": 0,
    "skip_next_signal": False,  # пропускать следующий сигнал входа
    "counter_trade_enabled": True,  # контр-трейд: открывать против сигнала
    "trades": [],  # список последних сделок
    "leverage": LEVERAGE,  # текущее плечо (изменяется через API)
    "pair_mode": None,  # 'top_gainer' | 'top_loser' | None
    "active_symbol": SYMBOL,  # активная торговая пара
}

class TradingBot:
    def __init__(self, telegram_notifier=None):
        self.notifier = telegram_notifier
        self.signal_sender = SignalSender()
        
        if USE_SIMULATOR:
            logging.info("Initializing market simulator")
            self.simulator = MarketSimulator(initial_price=3000, volatility=0.02)
            self.exchange = None
            self.public_exchange = None
        else:
            logging.info("Initializing MEXC exchange connection")
            self.simulator = None

            # Публичный клиент MEXC — для OHLCV/цены (без ключей)
            self.public_exchange = ccxt.mexc({
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            })

            # Торговый клиент MEXC — для ордеров (нужны ключи)
            self.exchange = ccxt.mexc({
                "apiKey": API_KEY,
                "secret": API_SECRET,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })

        # Load saved state first
        self.load_state_from_file()
        # Always enforce the configured leverage — never let the state file override it
        state["leverage"] = LEVERAGE

        if not USE_SIMULATOR and API_KEY and API_SECRET:
            open_type = 1 if ISOLATED else 2  # 1=isolated, 2=cross
            # Set leverage for both long (1) and short (2) positions
            for pos_type in [1, 2]:
                try:
                    self.exchange.set_leverage(LEVERAGE, SYMBOL, params={
                        "openType": open_type,
                        "positionType": pos_type
                    })
                    logging.info(f"Leverage x{LEVERAGE} set for positionType={pos_type} openType={open_type}")
                except Exception as e:
                    logging.warning(f"set_leverage positionType={pos_type}: {e}")
            # Fetch real balance from MEXC and override saved state
            try:
                balance_data = self.exchange.fetch_balance()
                usdt_free = float(balance_data.get('USDT', {}).get('free', 0) or balance_data.get('free', {}).get('USDT', 0) or 0)
                usdt_total = float(balance_data.get('USDT', {}).get('total', 0) or balance_data.get('total', {}).get('USDT', 0) or 0)
                if usdt_total > 0:
                    state['balance'] = usdt_total
                    state['available'] = usdt_free
                    logging.info(f"Real MEXC balance loaded: total={usdt_total:.2f} USDT, free={usdt_free:.2f} USDT")
            except Exception as e:
                logging.warning(f"Could not fetch real balance: {e}")
        
    def save_state_to_file(self):
        try:
            with open("goldantilopaeth500_state.json", "w") as f:
                json.dump(state, f, default=str, indent=2)
        except Exception as e:
            logging.error(f"Save error: {e}")

    def load_state_from_file(self):
        try:
            with open("goldantilopaeth500_state.json", "r") as f:
                data = json.load(f)
                state.update(data)
            # Если позиция открыта, но entry_time слишком старый (> 2 часов) — сбрасываем
            if state.get("in_position") and state.get("position"):
                entry_time_str = state["position"].get("entry_time", "")
                try:
                    entry_dt = datetime.fromisoformat(entry_time_str)
                    age_hours = (datetime.utcnow() - entry_dt).total_seconds() / 3600
                    if age_hours > 2:
                        logging.warning(f"Stale position detected (age: {age_hours:.1f}h), resetting.")
                        state["in_position"] = False
                        state["position"] = None
                        # Возвращаем маржу обратно в available
                        state["available"] = state["balance"]
                except Exception:
                    state["in_position"] = False
                    state["position"] = None
                    state["available"] = state["balance"]
        except:
            pass

    def now(self):
        return datetime.utcnow()

    @staticmethod
    def symbol_to_mexc(symbol: str) -> str:
        """Convert any symbol format to MEXC API format: SOL/USDT:USDT or SOL_USDT → SOLUSDT"""
        s = symbol.split(':')[0]   # remove :USDT suffix
        s = s.replace('/', '').replace('_', '').replace('-', '')
        return s.upper()

    def fetch_ohlcv_tf(self, tf: str, limit=200, symbol: str = None):
        mexc_sym = self.symbol_to_mexc(symbol) if symbol else "SOLUSDT"
        ccxt_sym = symbol.split(':')[0] if symbol else SYMBOL_SPOT
        try:
            if USE_SIMULATOR and self.simulator:
                ohlcv = self.simulator.fetch_ohlcv(tf, limit=limit)
            else:
                ohlcv = None
                # 1) MEXC REST API напрямую
                try:
                    ohlcv = fetch_ohlcv_mexc(mexc_sym, interval=tf, limit=limit)
                    logging.debug(f"MEXC OHLCV {tf} ({mexc_sym}): {len(ohlcv)} candles")
                except Exception as e1:
                    logging.warning(f"MEXC REST OHLCV {tf} failed: {e1}")
                    # 2) ccxt.mexc (резерв)
                    try:
                        exc = self.public_exchange if self.public_exchange else self.exchange
                        ohlcv = exc.fetch_ohlcv(ccxt_sym, timeframe=tf, limit=limit)
                        logging.info(f"ccxt MEXC OHLCV {tf} ({ccxt_sym}): {len(ohlcv)} candles")
                    except Exception as e2:
                        logging.error(f"MEXC OHLCV {tf} failed (REST + ccxt): {e2}")

            if not ohlcv or len(ohlcv) < 5:
                return None

            df = pd.DataFrame(ohlcv)
            df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            logging.error(f"fetch_ohlcv_tf {tf} error: {e}")
            return None

    def compute_psar(self, df: pd.DataFrame):
        if df is None or len(df) < 5:
            return None
        try:
            psar_ind = PSARIndicator(high=df["high"].astype(float), low=df["low"].astype(float), close=df["close"].astype(float), step=0.05, max_step=0.5)
            return psar_ind.psar()
        except Exception as e:
            logging.error(f"PSAR compute error: {e}")
            return None

    def get_direction_from_psar(self, df: pd.DataFrame):
        psar = self.compute_psar(df)
        if psar is None or len(psar) == 0:
            return None
        last_psar = psar.iloc[-1]
        last_close = float(df["close"].iloc[-1])
        if pd.isna(last_psar):
            return None
        return "long" if last_close > last_psar else "short"

    def get_current_directions(self):
        active_sym = state.get("active_symbol") or None
        directions = {}
        for tf in TIMEFRAMES.keys():
            df = self.fetch_ohlcv_tf(tf, symbol=active_sym)
            directions[tf] = self.get_direction_from_psar(df) if df is not None else None
        return directions

    def compute_order_size_usdt(self, balance, price):
        lev = state.get("leverage", LEVERAGE)
        notional = balance * POSITION_PERCENT * lev
        base_amount = notional / price
        return base_amount, notional

    def place_market_order(self, side: str, amount_base: float):
        if RUN_IN_PAPER or not API_KEY:
            price = self.get_current_price()
            entry_time = self.now()
            notional = amount_base * price
            lev = state.get("leverage", LEVERAGE)
            margin = notional / lev
            open_fee = notional * 0.0003
            state["available"] -= (margin + open_fee)
            state["available"] = max(0.0, state["available"])
            state["balance"] -= open_fee
            state["balance"] = max(0.0, state["balance"])
            
            if "telegram_trade_counter" not in state:
                state["telegram_trade_counter"] = 1
            else:
                state["telegram_trade_counter"] += 1
            
            state["in_position"] = True
            lev = state.get("leverage", LEVERAGE)
            pos_side = "long" if side == "buy" else "short"
            mmr = 0.005  # maintenance margin rate ~0.5%
            if pos_side == "long":
                liq = smart_round(price * (1 - 1/lev + mmr))
            else:
                liq = smart_round(price * (1 + 1/lev - mmr))
            state["position"] = {
                "side": pos_side,
                "entry_price": price,
                "size_base": amount_base,
                "notional": notional,
                "margin": margin,
                "entry_time": entry_time.isoformat(),
                "trade_number": state["telegram_trade_counter"],
                "symbol": state.get("active_symbol", "SOL_USDT"),
                "leverage": lev,
                "liquidation_price": liq
            }
            
            # --- Тейк-профит 30% ROI (paper mode) ---
            tp_delta = price * (self.TP_ROI / lev)
            tp_price = smart_round(price + tp_delta) if pos_side == "long" else smart_round(price - tp_delta)
            state["take_profit_price"] = tp_price
            state["take_profit_contracts"] = None
            logging.info(f"TP target (paper): ${tp_price:.4f} (entry=${price:.4f}, ROI=30%, lev={lev})")

            if self.notifier:
                self.notifier.send_position_opened(state["position"], price, state["position"]["trade_number"], state["balance"])
            
            if side == "buy": self.signal_sender.send_open_long()
            else: self.signal_sender.send_open_short()
            
            return state["position"]
        else:
            try:
                open_type = 1 if ISOLATED else 2  # 1=isolated, 2=cross
                order_params = {"openType": open_type}
                trading_sym = self.get_trading_symbol()
                # Логируем точный amount перед отправкой
                logging.info(f"PLACING ORDER: side={side}, SYMBOL={trading_sym}, params={order_params}")
                # Проверяем contractSize на бирже
                try:
                    mkt = self.exchange.market(trading_sym)
                    cs = mkt.get('contractSize', 1)
                    min_amt = mkt.get('limits', {}).get('amount', {}).get('min', 1)
                    logging.info(f"Market contractSize={cs}, min_amount={min_amt}")
                    # Если contractSize != 1, нужно конвертировать
                    contracts = round(amount_base / cs) if cs and cs != 1 else amount_base
                    logging.info(f"Contracts to send: {contracts} (amount_base={amount_base:.6f} / contractSize={cs})")
                except Exception as me:
                    contracts = amount_base
                    logging.warning(f"Market info error: {me}")
                order = self.exchange.create_market_buy_order(trading_sym, contracts, params=order_params) if side == "buy" else self.exchange.create_market_sell_order(trading_sym, contracts, params=order_params)
                price = self.get_price_from_order(order)
                entry_time = self.now()
                notional = amount_base * price
                lev = state.get("leverage", LEVERAGE)
                margin = notional / lev
                open_fee = notional * 0.0003
                state["available"] -= (margin + open_fee)
                state["balance"] -= open_fee
                state["in_position"] = True
                pos_side = "long" if side == "buy" else "short"
                state["position"] = {
                    "side": pos_side,
                    "entry_price": price,
                    "size_base": amount_base,
                    "notional": notional,
                    "margin": margin,
                    "entry_time": entry_time.isoformat(),
                    "symbol": state.get("active_symbol", "SOL_USDT")
                }
                # --- Тейк-профит при 30% ROI ---
                tp_delta = price * (self.TP_ROI / lev)
                tp_price = smart_round(price + tp_delta) if pos_side == "long" else smart_round(price - tp_delta)
                state["take_profit_price"] = tp_price
                state["take_profit_contracts"] = int(contracts)
                state["trailing_stop_price"] = None  # убрать старый стоп
                logging.info(f"TP target: ${tp_price:.4f} (entry=${price:.4f}, ROI=30%, lev={lev})")
                self.place_take_profit_order(pos_side, int(contracts), tp_price)
                if side == "buy": self.signal_sender.send_open_long()
                else: self.signal_sender.send_open_short()
                return state["position"]
            except Exception as e:
                logging.error(f"Order error: {e}")
                return None

    def get_price_from_order(self, order):
        if not order: return self.get_current_price()
        for field in ['average', 'price']:
            if order.get(field): return float(order[field])
        info = order.get('info', {})
        for field in ['avgPrice', 'price']:
            if info.get(field): return float(info[field])
        return self.get_current_price()

    def close_position(self, close_reason="unknown"):
        if not state["in_position"]: return None
        side = state["position"]["side"]
        size = state["position"]["size_base"]
        
        if RUN_IN_PAPER or not API_KEY:
            price = self.get_current_price()
            entry_price = state["position"]["entry_price"]
            close_notional = size * price
            pnl = (price - entry_price) * size if side == "long" else (entry_price - price) * size
            close_fee = close_notional * 0.0003
            pnl -= close_fee

            state["available"] += state["position"]["margin"] + pnl
            state["available"] = max(0.0, state["available"])
            state["balance"] = state["available"]
            
            trade = {
                "time": self.now().isoformat(),
                "side": side,
                "entry_price": entry_price,
                "exit_price": price,
                "size_base": size,
                "pnl": pnl,
                "duration": self.calculate_duration(state["position"]["entry_time"]),
                "close_reason": close_reason
            }
            
            if self.notifier:
                self.notifier.send_position_closed(trade, state["position"].get("trade_number", 1), state["balance"])
            
            self.append_trade(trade)
            state["in_position"] = False
            state["position"] = None
            self.save_state_to_file()
            return trade
        else:
            try:
                # size_contracts — число контрактов MEXC (ccxt передаёт напрямую как vol)
                size_contracts = state["position"].get("size_contracts")
                if not size_contracts:
                    # фолбэк: конвертируем SOL → контракты
                    try:
                        cs = float(self.exchange.market(SYMBOL).get('contractSize') or 0.1)
                    except Exception:
                        cs = 0.1
                    size_contracts = round(size / cs)
                logging.info(f"CLOSING ORDER: side={side}, size={size:.4f} SOL = {size_contracts} contracts")
                # reduceOnly=True — только закрытие, не открываем обратную
                close_params = {"reduceOnly": True}
                order = self.exchange.create_market_sell_order(SYMBOL, size_contracts, params=close_params) if side == "long" else self.exchange.create_market_buy_order(SYMBOL, size_contracts, params=close_params)
                exit_price = self.get_price_from_order(order)
                entry_price = state["position"]["entry_price"]
                close_notional = size * exit_price
                pnl = (exit_price - entry_price) * size if side == "long" else (entry_price - exit_price) * size
                pnl -= close_notional * 0.0003
                state["available"] += state["position"]["margin"] + pnl
                state["available"] = max(0.0, state["available"])
                state["balance"] = state["available"]
                trade = {
                    "time": self.now().isoformat(),
                    "side": side,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "duration": self.calculate_duration(state["position"]["entry_time"]),
                    "close_reason": close_reason
                }
                self.append_trade(trade)
                state["in_position"] = False
                state["position"] = None
                self.save_state_to_file()
                return trade
            except Exception as e:
                logging.error(f"Close error: {e}")
                return None

    def calculate_duration(self, entry_time_str):
        try:
            duration = self.now() - datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
            m, s = divmod(int(duration.total_seconds()), 60)
            return f"{m}м {s}с" if m > 0 else f"{s}с"
        except: return "N/A"

    def append_trade(self, trade):
        state["trades"].insert(0, trade)
        state["trades"] = state["trades"][:DASHBOARD_MAX]

    def fetch_real_position(self):
        """Получить реальную открытую позицию с биржи MEXC"""
        try:
            if not self.exchange or not API_KEY:
                return None
            positions = self.exchange.fetch_positions([SYMBOL])
            for pos in positions:
                contracts_count = float(pos.get('contracts') or 0)  # кол-во контрактов (MEXC native)
                if contracts_count > 0:
                    entry_price = float(pos.get('entryPrice') or 0)
                    side = pos.get('side', 'long')
                    lev = float(pos.get('leverage') or state.get('leverage', LEVERAGE))

                    # contractSize = 0.1 SOL/контракт на MEXC SOL/USDT
                    try:
                        cs = float(self.exchange.market(SYMBOL).get('contractSize') or 1)
                    except Exception:
                        cs = 0.1
                    size_sol = contracts_count * cs  # реальный объём в SOL

                    # mark_price: берём текущую цену из state
                    mark_price = state.get('live_price') or float(pos.get('markPrice') or entry_price)

                    # Notional = size_sol × mark_price (как показывает MEXC)
                    notional = size_sol * mark_price

                    # Initial Margin = notional / leverage
                    margin = notional / lev if lev else notional

                    # unrealized P&L по формуле MEXC (от entry до mark)
                    if side == 'long':
                        upnl = (mark_price - entry_price) * size_sol
                    else:
                        upnl = (entry_price - mark_price) * size_sol

                    liq = float(pos.get('liquidationPrice') or 0)

                    # Время открытия позиции из raw info MEXC
                    raw_info = pos.get('info', {})
                    create_ts = raw_info.get('createTime')
                    if create_ts:
                        from datetime import timezone
                        entry_time = datetime.fromtimestamp(int(create_ts)/1000, tz=timezone.utc).isoformat()
                    else:
                        entry_time = state.get('position', {}).get('entry_time') if state.get('position') else None

                    return {
                        'side': side,
                        'entry_price': entry_price,
                        'size_base': size_sol,           # SOL для отображения
                        'size_contracts': int(contracts_count),  # контракты для ордеров
                        'notional': notional,
                        'margin': margin,
                        'leverage': lev,
                        'unrealized_pnl': upnl,
                        'liquidation_price': liq,
                        'mark_price': mark_price,
                        'entry_time': entry_time,
                    }
            return None
        except Exception as e:
            logging.warning(f"fetch_real_position error: {e}")
            return None

    TP_ROI = 0.50  # Тейк-профит при ROI 50%

    def place_take_profit_order(self, side, size_contracts, tp_price):
        """Разместить тейк-профит ордер на MEXC при открытии позиции."""
        close_side = 'sell' if side == 'long' else 'buy'
        # Попытка 1: stop-limit через MEXC-параметры
        try:
            params = {
                'stopPrice': tp_price,
                'reduceOnly': True,
                'planType': 'profit_loss',   # MEXC futures TP plan
            }
            order = self.exchange.create_order(
                SYMBOL, 'limit', close_side, size_contracts, tp_price, params
            )
            logging.info(f"TP ORDER placed (limit): {close_side} {size_contracts} @ ${tp_price:.4f} | id={order.get('id')}")
            return order
        except Exception as e1:
            logging.warning(f"TP limit attempt failed: {e1}")
        # Попытка 2: обычный limit reduceOnly
        try:
            params2 = {'reduceOnly': True, 'timeInForce': 'GTC'}
            order = self.exchange.create_order(
                SYMBOL, 'limit', close_side, size_contracts, tp_price, params2
            )
            logging.info(f"TP ORDER placed (limit GTC): {close_side} {size_contracts} @ ${tp_price:.4f} | id={order.get('id')}")
            return order
        except Exception as e2:
            logging.warning(f"TP GTC attempt failed: {e2}")
        logging.warning("TP exchange order failed — software fallback active")
        return None

    def place_stop_order(self, side, size_contracts, stop_price):
        """
        Разместить стоп-маркет ордер на MEXC для закрытия позиции.
        side: 'long' или 'short' — сторона текущей позиции.
        size_contracts: кол-во контрактов для закрытия.
        stop_price: цена триггера.
        Возвращает order dict или None если не удалось.
        """
        try:
            close_side = 'sell' if side == 'long' else 'buy'
            params = {
                'stopPrice': stop_price,
                'reduceOnly': True,
            }
            order = self.exchange.create_order(
                SYMBOL,
                'stop_market',
                close_side,
                size_contracts,
                None,
                params
            )
            logging.info(f"STOP ORDER placed: {close_side} {size_contracts} contracts @ trigger {stop_price:.4f} | id={order.get('id')}")
            return order
        except Exception as e:
            logging.warning(f"place_stop_order failed (will use software stop): {e}")
            return None

    def get_trading_symbol(self) -> str:
        """Convert active_symbol from state to CCXT perpetual futures format."""
        sym = state.get("active_symbol", SYMBOL)
        if ':' in sym:          # already SOL/USDT:USDT
            return sym
        if '_' in sym:          # BLOCK_USDT → BLOCK/USDT:USDT
            base = sym.split('_')[0]
            return f"{base}/USDT:USDT"
        if '/' in sym:          # SOL/USDT → SOL/USDT:USDT
            base = sym.split('/')[0]
            return f"{base}/USDT:USDT"
        return SYMBOL

    def get_current_price(self):
        try:
            if USE_SIMULATOR:
                return self.simulator.get_current_price()
            # Build MEXC ticker symbol from active pair
            active = state.get("active_symbol", SYMBOL)
            mexc_sym = self.symbol_to_mexc(active)
            # 1) MEXC REST API
            try:
                return fetch_price_mexc(mexc_sym)
            except Exception as e1:
                logging.warning(f"MEXC REST price failed ({mexc_sym}): {e1}")
                # 2) ccxt.mexc (резерв)
                exc = self.public_exchange if self.public_exchange else self.exchange
                ccxt_sym = self.get_trading_symbol()
                try:
                    ticker = exc.fetch_ticker(ccxt_sym)
                except Exception:
                    ticker = exc.fetch_ticker(SYMBOL_SPOT)
                return float(ticker["last"])
        except Exception:
            return 150.0

    def strategy_loop(self, should_continue=lambda: True):
        while should_continue():
            try:
                dirs = self.get_current_directions()
                state["sar_directions"] = dirs
                if any(d is None for d in dirs.values()):
                    time.sleep(5)
                    continue

                active_tf = state.get("signal_timeframe", "1m")

                COMBO_MAP = {
                    "1m":     ["1m"],
                    "5m":     ["5m"],
                    "15m":    ["15m"],
                    "30m":    ["30m"],
                    "1+5":    ["1m", "5m"],
                    "1+15":   ["1m", "15m"],
                    "5+15":   ["5m", "15m"],
                    "1+5+15": ["1m", "5m", "15m"],
                    "ALL":    ["1m", "5m", "15m", "30m"],
                }

                def get_combo_dir(combo_key):
                    tfs = COMBO_MAP.get(combo_key, ["1m"])
                    vals = [dirs.get(tf) for tf in tfs]
                    if vals and all(v == vals[0] and v is not None for v in vals):
                        return vals[0]
                    return None

                open_combo  = state.get("open_strategy",  "1m")
                close_combo = state.get("close_strategy", "1m")
                d1 = get_combo_dir(open_combo)   # direction for opening
                dc = get_combo_dir(close_combo)  # direction for closing
                logging.info(f"[{self.now()}] SAR: {', '.join(f'{tf}={dirs[tf]}' for tf in dirs)} | signal_tf={active_tf} | open={open_combo}({d1}) | close={close_combo}({dc})")

                counter = state.get("counter_trade_enabled", True)
                pair_mode = state.get("pair_mode")  # 'top_gainer' | 'top_loser' | None

                def calc_order_side(sar_dir):
                    """Сторона ордера с учётом режима контр/обычный"""
                    if counter:
                        return "sell" if sar_dir == "long" else "buy"
                    else:
                        return "buy" if sar_dir == "long" else "sell"

                def allowed_to_open(sar_dir):
                    """Разрешено ли открывать позицию в данном направлении по вкладке пары"""
                    if pair_mode == 'top_gainer':
                        return sar_dir == 'long'   # Выросла → только LONG
                    elif pair_mode == 'top_loser':
                        return sar_dir == 'short'  # Упала → только SHORT
                    return True  # вкладка не выбрана — любое направление

                if state["in_position"]:
                    pos_side = state["position"]["side"]
                    if counter:
                        # Контр-трейд: закрываем когда сигнал закрытия совпадает с нашей позицией
                        close_cond = dc is not None and (dc == pos_side)
                    else:
                        # Обычный трейд: закрываем когда сигнал закрытия разворачивается против позиции
                        close_cond = dc is not None and (dc != pos_side)
                    if close_cond:
                        logging.info(f"SAR flip → закрываем {pos_side}, close_combo={close_combo}({dc})")
                        self.close_position(close_reason="sar_reversal")
                        # Флип только если разрешён по выбранной вкладке
                        if d1 and allowed_to_open(d1):
                            price = self.get_current_price()
                            size, _ = self.compute_order_size_usdt(state["balance"], price)
                            self.place_market_order(calc_order_side(d1), size)
                        else:
                            logging.info(f"Флип заблокирован: pair_mode={pair_mode}, d1={d1}")
                        self.save_state_to_file()
                else:
                    # Позиции нет: обновляем last_1m_dir
                    # Если была TP-пауза — ждём смены направления
                    if state["last_1m_dir"] and state["last_1m_dir"] != d1:
                        state["skip_next_signal"] = False
                    state["last_1m_dir"] = d1

                    # Открываем только если все TF из open_combo согласны и направление разрешено
                    if d1 and not state["skip_next_signal"] and allowed_to_open(d1):
                        price = self.get_current_price()
                        size, _ = self.compute_order_size_usdt(state["balance"], price)
                        self.place_market_order(calc_order_side(d1), size)
                        self.save_state_to_file()
                    elif d1 and not allowed_to_open(d1):
                        logging.info(f"Вход заблокирован: pair_mode={pair_mode}, d1={d1}")
                
                time.sleep(5)
            except Exception as e:
                logging.error(f"Strategy loop error: {e}")
                time.sleep(5)
