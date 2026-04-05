"""
InvoMirror Bot - Trade State Manager
=======================================
Tracks mirrored positions and persists state to disk.
Survives bot restarts.
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("invo_mirror.state")

STATE_FILE = "trade_state.json"


class TradeState:
    """Manages the state of mirrored trades."""

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = {
            "open_positions": {},
            "closed_positions": [],
            "known_invo_ids": set(),
            "skipped_invo_ids": set(),   # SHORT signals we've already logged
            "stats": {
                "total_trades": 0,
                "total_pnl": 0.0,
                "wins": 0,
                "losses": 0,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        self._load()

    def _load(self):
        """Load state from disk."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                self.state["open_positions"] = data.get("open_positions", {})
                self.state["closed_positions"] = data.get("closed_positions", [])
                self.state["known_invo_ids"] = set(data.get("known_invo_ids", []))
                self.state["skipped_invo_ids"] = set(data.get("skipped_invo_ids", []))
                self.state["stats"] = data.get("stats", self.state["stats"])
                logger.info(
                    f"Loaded state: {len(self.state['open_positions'])} open, "
                    f"{len(self.state['closed_positions'])} closed, "
                    f"{len(self.state['skipped_invo_ids'])} skipped"
                )
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def _save(self):
        """Persist state to disk."""
        try:
            data = {
                "open_positions": self.state["open_positions"],
                "closed_positions": self.state["closed_positions"],
                "known_invo_ids": list(self.state["known_invo_ids"]),
                "skipped_invo_ids": list(self.state["skipped_invo_ids"]),
                "stats": self.state["stats"],
            }
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def is_known(self, invo_id: str) -> bool:
        """Check if we've already seen this Invo investment (opened or skipped)."""
        return invo_id in self.state["known_invo_ids"] or invo_id in self.state["skipped_invo_ids"]

    def mark_skipped(self, invo_id: str, reason: str = ""):
        """Mark an investment as seen but skipped (e.g., SHORT in LONG_ONLY mode)."""
        self.state["skipped_invo_ids"].add(invo_id)
        self._save()

    def clean_skipped(self, active_invo_ids: set):
        """Remove skipped IDs that are no longer active (trader closed them)."""
        stale = self.state["skipped_invo_ids"] - active_invo_ids
        if stale:
            self.state["skipped_invo_ids"] -= stale
            self._save()

    def record_open(self, invo_id: str, trade_details: dict):
        """Record a new mirrored position."""
        self.state["open_positions"][invo_id] = {
            **trade_details,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state["known_invo_ids"].add(invo_id)
        self.state["stats"]["total_trades"] += 1
        self._save()
        logger.info(f"Recorded OPEN: {trade_details.get('ticker')} (invo_id: {invo_id})")

    def record_close(self, invo_id: str, close_details: dict):
        """Record a closed position."""
        if invo_id not in self.state["open_positions"]:
            logger.warning(f"Attempted to close unknown position: {invo_id}")
            return

        open_trade = self.state["open_positions"].pop(invo_id)
        closed_trade = {
            **open_trade,
            **close_details,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }

        status = close_details.get("status", "")
        pnl = close_details.get("pnl", 0)
        if not status.startswith("SELL_FAILED"):
            self.state["stats"]["total_pnl"] += pnl
            if pnl > 0:
                self.state["stats"]["wins"] += 1
            else:
                self.state["stats"]["losses"] += 1

        self.state["closed_positions"].append(closed_trade)
        self._save()
        logger.info(f"Recorded CLOSE: {open_trade.get('ticker')} PnL: ${pnl:.2f}")

    def get_open_positions(self) -> dict:
        return self.state["open_positions"]

    def get_open_position(self, invo_id: str) -> Optional[dict]:
        return self.state["open_positions"].get(invo_id)

    def get_open_count(self) -> int:
        return len(self.state["open_positions"])

    def get_stats(self) -> dict:
        stats = self.state["stats"].copy()
        total = stats["wins"] + stats["losses"]
        stats["win_rate"] = (stats["wins"] / total * 100) if total > 0 else 0
        stats["open_count"] = self.get_open_count()
        stats["closed_count"] = len(self.state["closed_positions"])
        return stats
