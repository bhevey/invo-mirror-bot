"""
InvoMirror Bot - Telegram Notifications
==========================================
Sends wallet position updates to Telegram.
"""

import logging
import requests

logger = logging.getLogger("invo_mirror.telegram")


class TelegramNotifier:
    """Sends messages via Telegram Bot API."""

    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        if not self.enabled:
            logger.info("Telegram notifications disabled (no token/chat_id)")

    def send(self, message: str) -> bool:
        """Send a message to the configured Telegram chat."""
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                self.API_URL.format(token=self.bot_token),
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return True
            logger.error(f"Telegram API error: {resp.status_code} {resp.text}")
            return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def send_wallet_update(self, wallet_value: float, aud_value: float | None,
                           starting_balance: float, stats: dict,
                           positions: list[dict]) -> bool:
        """Send a formatted wallet position update."""
        pnl = wallet_value - starting_balance
        pnl_pct = ((wallet_value - starting_balance) / starting_balance) * 100
        pnl_emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
        pnl_sign = "+" if pnl >= 0 else ""

        lines = [
            "\U0001f4ca <b>InvoMirror Wallet Update</b>",
            "",
            f"\U0001f4b0 <b>${wallet_value:.2f} USDT</b>",
        ]
        if aud_value:
            lines.append(f"\U0001f1e6\U0001f1fa A${aud_value:.2f} AUD")
        lines.extend([
            f"{pnl_emoji} P&amp;L: <b>{pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)</b>",
            f"\U0001f3af Started: ${starting_balance:.2f} USDT",
            "",
            f"Open: {stats.get('open_count', 0)} | "
            f"Closed: {stats.get('closed_count', 0)} | "
            f"Win rate: {stats.get('win_rate', 0):.0f}%",
        ])

        if positions:
            lines.append("")
            for pos in positions:
                ticker = pos.get("ticker", "?")
                change_pct = pos.get("change_pct", 0)
                sign = "+" if change_pct >= 0 else ""
                emoji = "\U00002705" if change_pct >= 0 else "\U0000274c"
                cost = pos.get("cost", 0)
                lines.append(f"  {emoji} {ticker}: {sign}{change_pct:.1f}% (${cost:.2f})")

        return self.send("\n".join(lines))
