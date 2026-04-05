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
from datetime import datetime, timezone

import config
from invo_client import InvoClient
from binance_client import BinanceClient
from trade_state import TradeState

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

        logger.info(f"InvoMirror Bot initialized in {self.mode.upper()} mode")
        logger.info(f"Stop-loss: {self.stop_loss_pct * 100:.0f}%")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Max positions: {config.MAX_OPEN_POSITIONS}")

    def _calculate_trade_amount(self) -> float:
        """Calculate how much USDT to spend on a trade."""
        if self.mode == "paper":
            paper_balance = getattr(config, "PAPER_BALANCE", 200.0)
            amount = paper_balance * config.TRADE_ALLOCATION_PCT
            amount = max(amount, config.MIN_TRADE_AMOUNT_USDT)
            amount = min(amount, config.MAX_TRADE_AMOUNT_USDT)
            return amount

        balance = self.binance.get_usdt_balance()
        amount = balance * config.TRADE_ALLOCATION_PCT
        amount = max(amount, config.MIN_TRADE_AMOUNT_USDT)
        amount = min(amount, config.MAX_TRADE_AMOUNT_USDT)

        if balance < config.MIN_TRADE_AMOUNT_USDT:
            logger.warning(f"Insufficient USDT balance: ${balance:.2f}")
            return 0

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

        amount = self._calculate_trade_amount()
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
            "invo_leverage": signal["leverage"],
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
            if result:
                filled_value = float(result.get("cummulativeQuoteQty", 0))
                cost = position.get("binance_total_cost", 0)
                pnl = filled_value - cost
                close_details["binance_sell_order_id"] = result.get("orderId")
                close_details["binance_sell_value"] = filled_value
                close_details["pnl"] = pnl
                close_details["status"] = "SOLD"
            else:
                logger.error(f"Failed to sell {ticker}")
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
        logger.info(f"Mode: {self.mode.upper()} | SL: {self.stop_loss_pct*100:.0f}% | Poll: {self.poll_interval}s")
        logger.info(f"Watching: {len(enabled_portfolios)} trader(s)")
        for p in enabled_portfolios:
            logger.info(f"  - {p['name']}")
        logger.info(f"Open: {stats['open_count']} | Closed: {stats['closed_count']} | "
                     f"Win rate: {stats['win_rate']:.1f}% | PnL: ${stats['total_pnl']:.2f}")

        if open_positions:
            for invo_id, pos in open_positions.items():
                buy_price = pos.get("binance_avg_price", 0)
                binance_symbol = pos.get("binance_symbol", "")
                sl_price = pos.get("stop_loss_price", 0)
                current_price = self._get_current_price(binance_symbol) if binance_symbol else 0
                if buy_price > 0 and current_price > 0:
                    change_pct = ((current_price - buy_price) / buy_price) * 100
                    logger.info(
                        f"  {pos['ticker']}: ${pos.get('binance_total_cost', 0):.2f} "
                        f"(now: {'+'if change_pct >= 0 else ''}{change_pct:.1f}%, "
                        f"SL: ${sl_price:.2f}) via {pos.get('invo_owner', '?')}"
                    )
        logger.info("=" * 60)

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
