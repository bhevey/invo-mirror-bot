"""
InvoMirror Bot - Main Engine
===============================
Monitors multiple Invo traders in parallel and mirrors their LONG trades
on Binance spot. Includes native stop-loss orders for live mode.

Fixes applied:
- SHORT signals logged once then suppressed (no spam)
- Auto re-login when token expires (uses email/password)
- Multiple portfolios monitored in parallel

Usage:
    python bot.py              # Run with config.py settings
    python bot.py --paper      # Force paper trading mode
    python bot.py --live       # Force live trading mode
"""

import sys
import time
import logging
import argparse
import requests
from datetime import datetime, timezone

import config
from invo_client import InvoClient
from binance_client import BinanceClient
from trade_state import TradeState
from telegram_notifier import TelegramNotifier

# ── Logging Setup ──
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("invo_mirror")


# ANSI color codes for terminal output
_GREEN = "\033[92m"
_RED = "\033[91m"
_RESET = "\033[0m"


def _get_usdt_aud_rate() -> float | None:
    """Fetch current USDT to AUD exchange rate from CoinGecko."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "tether", "vs_currencies": "aud"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("tether", {}).get("aud")
    except Exception:
        return None


def _color_pnl(text: str, value: float) -> str:
    """Color text green if positive, red if negative."""
    if value > 0:
        return f"{_GREEN}{text}{_RESET}"
    elif value < 0:
        return f"{_RED}{text}{_RESET}"
    return text


def _normalize_id(value) -> str:
    """Normalize a portfolio/investment ID for comparison.

    Invo's API returns IDs in canonical UUID form (with hyphens), but some
    config files or older state files may have them hyphen-stripped. This
    makes comparisons robust to either format. Returns "" for None/missing.
    """
    if value is None:
        return ""
    return str(value).replace("-", "").lower()


class InvoMirrorBot:
    """Main bot that monitors Invo and mirrors trades on Binance."""

    def __init__(self, mode: str = None):
        self.mode = mode or config.MODE
        self.invo = InvoClient(config)
        self.state = TradeState()

        if self.mode == "live":
            self.binance = BinanceClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)
        else:
            self.binance = None

        self.running = True
        self.stop_loss_pct = getattr(config, "STOP_LOSS_PCT", 0.15)
        self.poll_interval = getattr(config, "POLL_INTERVAL", 15)

        # Telegram notifications
        self.telegram = TelegramNotifier(
            getattr(config, "TELEGRAM_BOT_TOKEN", None),
            getattr(config, "TELEGRAM_CHAT_ID", None),
        )
        self.notify_interval = getattr(config, "TELEGRAM_NOTIFY_INTERVAL", 21600)
        self.last_notify_time = 0

        logger.info(f"InvoMirror Bot initialized in {self.mode.upper()} mode")
        logger.info(f"Stop-loss: {self.stop_loss_pct * 100:.0f}%")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Max positions: {config.MAX_OPEN_POSITIONS}")

    def _calculate_trade_amount(self, leverage: int = 1) -> float:
        """Calculate how much USDT to spend on a trade.

        If LEVERAGE_SCALING is enabled, the base amount is multiplied by the
        trader's leverage (capped by MAX_LEVERAGE_MULTIPLIER) to approximate
        their risk exposure on spot.
        """
        if self.mode == "paper":
            balance = getattr(config, "PAPER_BALANCE", 200.0)
        else:
            balance = self.binance.get_usdt_balance()
            if balance < config.MIN_TRADE_AMOUNT_USDT:
                logger.warning(f"Insufficient USDT balance: ${balance:.2f}")
                return 0

        amount = balance * config.TRADE_ALLOCATION_PCT

        # Scale by leverage if enabled
        if getattr(config, "LEVERAGE_SCALING", False) and leverage > 1:
            max_mult = getattr(config, "MAX_LEVERAGE_MULTIPLIER", 10)
            multiplier = min(leverage, max_mult)
            amount *= multiplier
            logger.info(f"Leverage scaling: {multiplier}x → ${amount:.2f} USDT")

        amount = max(amount, config.MIN_TRADE_AMOUNT_USDT)
        amount = min(amount, config.MAX_TRADE_AMOUNT_USDT)
        return amount

    def _get_current_price(self, binance_symbol: str) -> float:
        """Get current price from Binance (works in both modes)."""
        if self.mode == "live" and self.binance:
            price = self.binance.get_price(binance_symbol)
            if price:
                return price
        try:
            import requests as req
            resp = req.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": binance_symbol},
                timeout=5,
            )
            if resp.status_code == 200:
                return float(resp.json()["price"])
        except Exception:
            pass
        return 0

    def _place_native_stop_loss(self, symbol, quantity, buy_price):
        """Place a native stop-loss order on Binance (live mode only)."""
        if self.mode != "live" or not self.binance:
            return None
        stop_price = buy_price * (1 - self.stop_loss_pct)
        sell_price = stop_price * 0.99
        logger.info(
            f"Placing native stop-loss: {symbol} stop=${stop_price:.4f} limit=${sell_price:.4f}"
        )
        result = self.binance.place_stop_loss_order(symbol, quantity, stop_price, sell_price)
        if result:
            return result.get("orderId")
        logger.error(f"Failed to place native stop-loss for {symbol}")
        return None

    def _cancel_stop_loss(self, symbol, order_id):
        """Cancel a native stop-loss order."""
        if self.mode != "live" or not self.binance:
            return True
        result = self.binance.cancel_order(symbol, order_id)
        if result:
            return True
        status = self.binance.get_order_status(symbol, order_id)
        if status and status.get("status") == "FILLED":
            return True
        return False

    def _check_stop_loss_fills(self):
        """Check if any native stop-loss orders have been filled (live mode)."""
        if self.mode != "live" or not self.binance:
            return
        for invo_id, pos in list(self.state.get_open_positions().items()):
            stop_order_id = pos.get("stop_loss_order_id")
            if not stop_order_id:
                continue
            status = self.binance.get_order_status(pos["binance_symbol"], stop_order_id)
            if status and status.get("status") == "FILLED":
                filled_value = float(status.get("cummulativeQuoteQty", 0))
                cost = pos.get("binance_total_cost", 0)
                pnl = filled_value - cost
                logger.warning(f"STOP-LOSS FILLED: {pos['ticker']} — PnL: ${pnl:.2f}")
                self.state.record_close(invo_id, {
                    "binance_sell_value": filled_value,
                    "pnl": pnl,
                    "close_reason": "NATIVE_STOP_LOSS",
                    "status": "STOP_LOSS_FILLED",
                })

    def _check_paper_stop_losses(self):
        """Check stop-losses via polling (paper mode)."""
        for invo_id, pos in list(self.state.get_open_positions().items()):
            binance_symbol = pos.get("binance_symbol")
            buy_price = pos.get("binance_avg_price", 0)
            if not binance_symbol or buy_price <= 0:
                continue
            current_price = self._get_current_price(binance_symbol)
            if current_price <= 0:
                continue
            drawdown = (buy_price - current_price) / buy_price
            if drawdown >= self.stop_loss_pct:
                loss_pct = drawdown * 100
                logger.warning(
                    f"STOP-LOSS TRIGGERED: {pos.get('ticker')} down {loss_pct:.1f}% "
                    f"(bought @ ${buy_price:.4f}, now @ ${current_price:.4f})"
                )
                self._execute_sell(invo_id, {
                    "closing_price": current_price,
                    "current_price": current_price,
                    "close_reason": f"STOP_LOSS_{loss_pct:.1f}%",
                })

    def _execute_buy(self, signal: dict) -> bool:
        """Execute a buy order based on an Invo signal."""
        ticker = signal["ticker"]
        binance_symbol = BinanceClient.invo_ticker_to_binance(ticker)
        if not binance_symbol:
            logger.error(f"Cannot map Invo ticker '{ticker}' to Binance symbol")
            return False

        if config.LONG_ONLY and signal["direction"] != "LONG":
            # Mark as skipped so we don't log it every poll
            self.state.mark_skipped(signal["id"], reason=f"SHORT_{ticker}")
            logger.info(f"Skipping SHORT {ticker} from {signal.get('owner_username', '?')} (LONG_ONLY)")
            return False

        if self.state.get_open_count() >= config.MAX_OPEN_POSITIONS:
            logger.warning(f"Max positions ({config.MAX_OPEN_POSITIONS}) reached, skipping {ticker}")
            return False

        leverage = signal.get("leverage", 1) or 1
        amount = self._calculate_trade_amount(leverage=leverage)
        if amount <= 0:
            return False

        trade_record = {
            "ticker": ticker,
            "binance_symbol": binance_symbol,
            "binance_asset": ticker.upper(),
            "direction": signal["direction"],
            "invo_entry_price": signal["entry_price"],
            "invo_take_profit": signal["take_profit"],
            "invo_stop_loss": signal["stop_loss"],
            "invo_leverage": leverage,
            "invo_position_size_pct": signal.get("position_size_pct", 0),
            "invo_owner": signal["owner_username"],
            "invo_portfolio_id": signal["portfolio_id"],
            "intended_amount_usdt": amount,
        }

        if self.mode == "live":
            result = self.binance.market_buy(binance_symbol, amount)
            if result:
                filled_qty = float(result.get("executedQty", 0))
                filled_value = float(result.get("cummulativeQuoteQty", 0))
                avg_price = filled_value / filled_qty if filled_qty > 0 else 0
                trade_record["binance_order_id"] = result.get("orderId")
                trade_record["binance_qty"] = filled_qty
                trade_record["binance_avg_price"] = avg_price
                trade_record["binance_total_cost"] = filled_value
                trade_record["status"] = "FILLED"
                # Place native stop-loss
                stop_id = self._place_native_stop_loss(binance_symbol, filled_qty, avg_price)
                if stop_id:
                    trade_record["stop_loss_order_id"] = stop_id
                trade_record["stop_loss_price"] = avg_price * (1 - self.stop_loss_pct)
            else:
                logger.error(f"Failed to execute buy for {ticker}")
                trade_record["status"] = "FAILED"
                return False
        else:
            price = self._get_current_price(binance_symbol)
            if price <= 0:
                price = signal["entry_price"] or 0
            qty = amount / price if price > 0 else 0
            trade_record["binance_qty"] = qty
            trade_record["binance_avg_price"] = price
            trade_record["binance_total_cost"] = amount
            trade_record["stop_loss_price"] = price * (1 - self.stop_loss_pct)
            trade_record["status"] = "PAPER_FILLED"

        self.state.record_open(signal["id"], trade_record)
        logger.info(
            f"{'[PAPER] ' if self.mode == 'paper' else ''}"
            f"BUY {ticker} ({binance_symbol}) — "
            f"${trade_record.get('binance_total_cost', amount):.2f} USDT — "
            f"SL: ${trade_record.get('stop_loss_price', 0):.4f} — "
            f"Mirroring {signal.get('owner_username', '?')}"
        )
        return True

    def _execute_position_increase(self, invo_id: str, position: dict, old_pct: float, new_pct: float) -> bool:
        """Buy more of an asset when the trader increases their position size."""
        ticker = position["ticker"]
        binance_symbol = position["binance_symbol"]
        increase_ratio = (new_pct - old_pct) / old_pct if old_pct > 0 else 1.0
        current_cost = position.get("binance_total_cost", 0)
        additional_usdt = current_cost * increase_ratio

        if additional_usdt < config.MIN_TRADE_AMOUNT_USDT:
            logger.info(
                f"Position increase for {ticker} too small (${additional_usdt:.2f}), skipping"
            )
            position["invo_position_size_pct"] = new_pct
            self.state._save()
            return False

        additional_usdt = min(additional_usdt, config.MAX_TRADE_AMOUNT_USDT)

        logger.info(
            f"POSITION INCREASE: {ticker} {old_pct:.2f}% → {new_pct:.2f}% "
            f"(+{increase_ratio*100:.1f}%) — buying ${additional_usdt:.2f} more"
        )

        if self.mode == "live":
            result = self.binance.market_buy(binance_symbol, additional_usdt)
            if result:
                new_qty = float(result.get("executedQty", 0))
                new_value = float(result.get("cummulativeQuoteQty", 0))
                old_qty = position.get("binance_qty", 0)
                old_cost = position.get("binance_total_cost", 0)
                total_qty = old_qty + new_qty
                total_cost = old_cost + new_value
                avg_price = total_cost / total_qty if total_qty > 0 else 0

                position["binance_qty"] = total_qty
                position["binance_avg_price"] = avg_price
                position["binance_total_cost"] = total_cost
                position["invo_position_size_pct"] = new_pct

                # Update stop-loss for the new total quantity
                old_stop_id = position.get("stop_loss_order_id")
                if old_stop_id:
                    self._cancel_stop_loss(binance_symbol, old_stop_id)
                new_stop_id = self._place_native_stop_loss(binance_symbol, total_qty, avg_price)
                if new_stop_id:
                    position["stop_loss_order_id"] = new_stop_id
                position["stop_loss_price"] = avg_price * (1 - self.stop_loss_pct)

                self.state._save()
                logger.info(
                    f"INCREASED {ticker}: +{new_qty} units (${new_value:.2f}) — "
                    f"Total: {total_qty} units (${total_cost:.2f})"
                )
                return True
            else:
                logger.error(f"Failed to increase position for {ticker}")
                return False
        else:
            price = self._get_current_price(binance_symbol)
            new_qty = additional_usdt / price if price > 0 else 0
            old_qty = position.get("binance_qty", 0)
            old_cost = position.get("binance_total_cost", 0)
            position["binance_qty"] = old_qty + new_qty
            position["binance_total_cost"] = old_cost + additional_usdt
            position["binance_avg_price"] = (old_cost + additional_usdt) / (old_qty + new_qty) if (old_qty + new_qty) > 0 else 0
            position["invo_position_size_pct"] = new_pct
            position["stop_loss_price"] = position["binance_avg_price"] * (1 - self.stop_loss_pct)
            self.state._save()
            logger.info(f"[PAPER] INCREASED {ticker}: +${additional_usdt:.2f}")
            return True

    def _execute_position_decrease(self, invo_id: str, position: dict, old_pct: float, new_pct: float) -> bool:
        """Sell part of an asset when the trader decreases their position size."""
        ticker = position["ticker"]
        binance_symbol = position["binance_symbol"]
        decrease_ratio = (old_pct - new_pct) / old_pct if old_pct > 0 else 0
        total_qty = position.get("binance_qty", 0)
        sell_qty = total_qty * decrease_ratio

        if sell_qty <= 0:
            return False

        logger.info(
            f"POSITION DECREASE: {ticker} {old_pct:.2f}% → {new_pct:.2f}% "
            f"(-{decrease_ratio*100:.1f}%) — selling {sell_qty:.6f} units"
        )

        if self.mode == "live":
            result = self.binance.market_sell(binance_symbol, sell_qty)
            if result and not result.get("error"):
                sold_qty = float(result.get("executedQty", 0))
                sold_value = float(result.get("cummulativeQuoteQty", 0))
                remaining_qty = total_qty - sold_qty
                cost_sold = position.get("binance_total_cost", 0) * (sold_qty / total_qty) if total_qty > 0 else 0
                remaining_cost = position.get("binance_total_cost", 0) - cost_sold

                position["binance_qty"] = remaining_qty
                position["binance_total_cost"] = remaining_cost
                position["invo_position_size_pct"] = new_pct

                # Update stop-loss for the reduced quantity
                old_stop_id = position.get("stop_loss_order_id")
                if old_stop_id:
                    self._cancel_stop_loss(binance_symbol, old_stop_id)
                if remaining_qty > 0:
                    avg_price = position.get("binance_avg_price", 0)
                    new_stop_id = self._place_native_stop_loss(binance_symbol, remaining_qty, avg_price)
                    if new_stop_id:
                        position["stop_loss_order_id"] = new_stop_id

                self.state._save()
                pnl = sold_value - cost_sold
                logger.info(
                    f"DECREASED {ticker}: -{sold_qty} units (${sold_value:.2f}, "
                    f"PnL: {_color_pnl(f'${pnl:.2f}', pnl)}) — "
                    f"Remaining: {remaining_qty} units (${remaining_cost:.2f})"
                )
                return True
            else:
                logger.error(f"Failed to decrease position for {ticker}")
                return False
        else:
            price = self._get_current_price(binance_symbol)
            sold_value = sell_qty * price if price > 0 else 0
            cost_sold = position.get("binance_total_cost", 0) * decrease_ratio
            position["binance_qty"] = total_qty - sell_qty
            position["binance_total_cost"] = position.get("binance_total_cost", 0) - cost_sold
            position["invo_position_size_pct"] = new_pct
            self.state._save()
            logger.info(f"[PAPER] DECREASED {ticker}: -${sold_value:.2f}")
            return True

    def _execute_sell(self, invo_id: str, signal: dict) -> bool:
        """Execute a sell order."""
        position = self.state.get_open_position(invo_id)
        if not position:
            return False

        ticker = position["ticker"]
        binance_symbol = position["binance_symbol"]
        qty = position.get("binance_qty", 0)
        close_reason = signal.get("close_reason", "INVO_TRADER_CLOSED")
        stop_order_id = position.get("stop_loss_order_id")

        close_details = {
            "invo_closing_price": signal.get("closing_price"),
            "close_reason": close_reason,
        }

        if self.mode == "live" and qty > 0:
            if stop_order_id and "STOP_LOSS" not in close_reason:
                self._cancel_stop_loss(binance_symbol, stop_order_id)
            result = self.binance.market_sell(binance_symbol, qty)
            if result and result.get("error"):
                error_code = result.get("code")
                error_msg = result.get("msg", "Unknown error")
                if error_code == -2010:
                    logger.warning(
                        f"Insufficient balance to sell {ticker} — closing position as unsellable. "
                        f"Asset may have been sold manually or never purchased."
                    )
                    close_details["status"] = "SELL_FAILED_NO_BALANCE"
                    close_details["error"] = error_msg
                else:
                    sell_failures = position.get("sell_failures", 0) + 1
                    max_retries = 3
                    if sell_failures >= max_retries:
                        logger.error(
                            f"Failed to sell {ticker} after {sell_failures} attempts "
                            f"(error {error_code}: {error_msg}) — giving up"
                        )
                        close_details["status"] = f"SELL_FAILED_{error_code}"
                        close_details["error"] = error_msg
                    else:
                        logger.error(
                            f"Failed to sell {ticker} (error {error_code}: {error_msg}) — "
                            f"will retry ({sell_failures}/{max_retries})"
                        )
                        position["sell_failures"] = sell_failures
                        self.state._save()
                        return False
            elif result:
                filled_value = float(result.get("cummulativeQuoteQty", 0))
                cost = position.get("binance_total_cost", 0)
                pnl = filled_value - cost
                close_details["binance_sell_order_id"] = result.get("orderId")
                close_details["binance_sell_value"] = filled_value
                close_details["pnl"] = pnl
                close_details["status"] = "SOLD"
            else:
                sell_failures = position.get("sell_failures", 0) + 1
                max_retries = 3
                if sell_failures >= max_retries:
                    logger.error(f"Failed to sell {ticker} after {sell_failures} attempts — giving up")
                    close_details["status"] = "SELL_FAILED_UNKNOWN"
                else:
                    logger.error(f"Failed to sell {ticker} — will retry ({sell_failures}/{max_retries})")
                    position["sell_failures"] = sell_failures
                    self.state._save()
                    return False
        else:
            current_price = signal.get("current_price") or signal.get("closing_price") or 0
            if current_price <= 0:
                current_price = self._get_current_price(binance_symbol)
            sell_value = qty * current_price if qty > 0 else 0
            cost = position.get("binance_total_cost", 0)
            pnl = sell_value - cost
            close_details["binance_sell_value"] = sell_value
            close_details["pnl"] = pnl
            close_details["status"] = "PAPER_SOLD"

        self.state.record_close(invo_id, close_details)
        status = close_details.get("status", "")
        if status.startswith("SELL_FAILED"):
            logger.warning(f"CLOSED {ticker} with status {status} — {close_reason}")
        else:
            logger.info(
                f"{'[PAPER] ' if self.mode == 'paper' else ''}"
                f"SELL {ticker} — PnL: ${close_details.get('pnl', 0):.2f} — {close_reason}"
            )
        return True

    def poll_portfolio(self, portfolio_config: dict):
        """Poll a single Invo portfolio for new/closed trades."""
        portfolio_id = portfolio_config["id"]
        portfolio_name = portfolio_config["name"]

        investments = self.invo.get_investments(portfolio_id)
        if investments is None:
            logger.warning(f"Failed to fetch investments for {portfolio_name}")
            return

        current_invo_ids = set()

        for inv in investments:
            parsed = self.invo.parse_investment(inv)
            if not parsed["is_open"] or not parsed["active"]:
                continue

            current_invo_ids.add(parsed["id"])

            # Only process if we haven't seen this investment before
            if not self.state.is_known(parsed["id"]):
                logger.info(
                    f"NEW SIGNAL from {portfolio_name}: "
                    f"{parsed['direction']} {parsed['ticker']} @ ${parsed['entry_price']}"
                )
                self._execute_buy(parsed)
            else:
                # Check for position size changes on existing open positions
                position = self.state.get_open_position(parsed["id"])
                if position:
                    old_pct = position.get("invo_position_size_pct", 0)
                    new_pct = parsed.get("position_size_pct", 0)
                    if old_pct > 0 and new_pct > 0 and abs(new_pct - old_pct) / old_pct > 0.05:
                        if new_pct > old_pct:
                            self._execute_position_increase(parsed["id"], position, old_pct, new_pct)
                        else:
                            self._execute_position_decrease(parsed["id"], position, old_pct, new_pct)

        # Clean up skipped IDs for positions that are no longer active
        self.state.clean_skipped(current_invo_ids)

        # Check for closed positions
        normalized_portfolio_id = _normalize_id(portfolio_id)
        for invo_id, pos in list(self.state.get_open_positions().items()):
            if _normalize_id(pos.get("invo_portfolio_id")) != normalized_portfolio_id:
                continue
            if invo_id not in current_invo_ids:
                logger.info(f"CLOSE SIGNAL: {pos['ticker']} closed by {portfolio_name}")
                self._execute_sell(invo_id, pos)

    def print_status(self):
        """Print current bot status."""
        stats = self.state.get_stats()
        open_positions = self.state.get_open_positions()
        enabled_portfolios = [p for p in config.WATCHED_PORTFOLIOS if p.get("enabled", True)]

        logger.info("=" * 60)
        logger.info(f"InvoMirror Status — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

        # Wallet position
        if self.mode == "live":
            wallet_value = self.binance.get_total_wallet_value()
            if wallet_value is not None:
                starting = config.STARTING_BALANCE_USDT
                wallet_pnl = wallet_value - starting
                wallet_pct = ((wallet_value - starting) / starting) * 100
                pnl_str = f"{'+'if wallet_pnl >= 0 else ''}${wallet_pnl:.2f} ({'+'if wallet_pct >= 0 else ''}{wallet_pct:.1f}%)"
                aud_rate = _get_usdt_aud_rate()
                aud_part = f" | A${wallet_value * aud_rate:.2f} AUD" if aud_rate else ""
                logger.info(
                    f"WALLET: ${wallet_value:.2f} USDT{aud_part} | "
                    f"Started: ${starting:.2f} | "
                    f"P&L: {_color_pnl(pnl_str, wallet_pnl)}"
                )

        logger.info(f"Mode: {self.mode.upper()} | SL: {self.stop_loss_pct*100:.0f}% | Poll: {self.poll_interval}s")
        logger.info(f"Watching: {len(enabled_portfolios)} trader(s)")
        for p in enabled_portfolios:
            logger.info(f"  - {p['name']}")
        trade_pnl = stats['total_pnl']
        trade_pnl_str = f"${trade_pnl:.2f}"
        logger.info(f"Open: {stats['open_count']} | Closed: {stats['closed_count']} | "
                     f"Win rate: {stats['win_rate']:.1f}% | PnL: {_color_pnl(trade_pnl_str, trade_pnl)}")

        if open_positions:
            for invo_id, pos in open_positions.items():
                buy_price = pos.get("binance_avg_price", 0)
                binance_symbol = pos.get("binance_symbol", "")
                sl_price = pos.get("stop_loss_price", 0)
                current_price = self._get_current_price(binance_symbol) if binance_symbol else 0
                if buy_price > 0 and current_price > 0:
                    change_pct = ((current_price - buy_price) / buy_price) * 100
                    pct_str = f"{'+'if change_pct >= 0 else ''}{change_pct:.1f}%"
                    logger.info(
                        f"  {pos['ticker']}: ${pos.get('binance_total_cost', 0):.2f} "
                        f"(now: {_color_pnl(pct_str, change_pct)}, "
                        f"SL: ${sl_price:.2f}) via {pos.get('invo_owner', '?')}"
                    )
        logger.info("=" * 60)

    def _send_telegram_update(self):
        """Send a wallet position update via Telegram."""
        if not self.telegram.enabled:
            return

        wallet_value = None
        if self.mode == "live" and self.binance:
            wallet_value = self.binance.get_total_wallet_value()

        if wallet_value is None:
            return

        aud_rate = _get_usdt_aud_rate()
        aud_value = wallet_value * aud_rate if aud_rate else None
        starting = config.STARTING_BALANCE_USDT
        stats = self.state.get_stats()

        # Build position list with current P&L
        positions = []
        for invo_id, pos in self.state.get_open_positions().items():
            buy_price = pos.get("binance_avg_price", 0)
            binance_symbol = pos.get("binance_symbol", "")
            current_price = self._get_current_price(binance_symbol) if binance_symbol else 0
            change_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 and current_price > 0 else 0
            positions.append({
                "ticker": pos.get("ticker", "?"),
                "change_pct": change_pct,
                "cost": pos.get("binance_total_cost", 0),
            })

        self.telegram.send_wallet_update(wallet_value, aud_value, starting, stats, positions)
        logger.info("Sent Telegram wallet update")

    def run(self):
        """Main bot loop."""
        logger.info("Starting InvoMirror Bot...")

        # Test connection on startup using the first watched portfolio
        first_portfolio_id = config.WATCHED_PORTFOLIOS[0]["id"]
        if not self.invo.test_connection(first_portfolio_id):
            logger.warning("Initial token invalid, attempting refresh...")
            if not self.invo._refresh_token():
                logger.error("Could not authenticate. Check INVO_ACCESS_TOKEN and INVO_REFRESH_TOKEN in config.py")
                return
            # Verify the refreshed token actually works
            if not self.invo.test_connection(first_portfolio_id):
                logger.error("Token refreshed but connection still failing. Check credentials in config.py")
                return

        self.print_status()
        poll_count = 0

        while self.running:
            try:
                for portfolio in config.WATCHED_PORTFOLIOS:
                    if not portfolio.get("enabled", True):
                        continue
                    self.poll_portfolio(portfolio)
                    time.sleep(1)  # Brief pause between portfolios

                # Check stop-losses
                if self.mode == "live":
                    self._check_stop_loss_fills()
                else:
                    self._check_paper_stop_losses()

                poll_count += 1
                if poll_count % 20 == 0:  # Status every ~5 min at 15s intervals
                    self.print_status()

                # Scheduled Telegram notification
                now = time.time()
                if self.telegram.enabled and (now - self.last_notify_time) >= self.notify_interval:
                    self._send_telegram_update()
                    self.last_notify_time = now

                time.sleep(self.poll_interval)

            except KeyboardInterrupt:
                logger.info("Shutdown requested (Ctrl+C)")
                self.running = False
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                time.sleep(30)

        logger.info("Bot stopped.")
        self.print_status()


def main():
    parser = argparse.ArgumentParser(description="InvoMirror Bot")
    parser.add_argument("--paper", action="store_true", help="Force paper trading mode")
    parser.add_argument("--live", action="store_true", help="Force live trading mode")
    args = parser.parse_args()

    mode = None
    if args.paper:
        mode = "paper"
    elif args.live:
        mode = "live"

    bot = InvoMirrorBot(mode=mode)
    bot.run()


if __name__ == "__main__":
    main()
