"""
InvoMirror Bot - Invo API Client
==================================
Handles all communication with the Invo (Involio) API.
"""

import time
import logging
import requests
from typing import Optional

logger = logging.getLogger("invo_mirror.invo_client")


class InvoClient:
    """Client for the Invo (Involio) API."""

    def __init__(self, config):
        self.base_url = config.INVO_API_BASE
        self.access_token = config.INVO_ACCESS_TOKEN
        self.refresh_token_str = getattr(config, "INVO_REFRESH_TOKEN", None)
        self.session = requests.Session()
        self._update_headers()

    def _update_headers(self):
        """Update session headers with current auth token."""
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Origin": "https://app.invoapp.com",
            "Referer": "https://app.invoapp.com/",
            "X-App-Release": "1.0.47",
            "X-App-Version": "0.0.75",
            "X-Platform": "web",
            "Timestamp": str(int(time.time() * 1000)),
        })

    def _request(self, endpoint: str, payload: dict = None, retries: int = 2) -> Optional[dict]:
        """Make a POST request to the Invo API with auto-retry and token refresh."""
        url = f"{self.base_url}/{endpoint}"

        for attempt in range(retries + 1):
            try:
                self.session.headers["Timestamp"] = str(int(time.time() * 1000))
                resp = self.session.post(url, json=payload or {}, timeout=30)

                if resp.status_code == 401 and attempt < retries:
                    logger.warning("Auth token expired, attempting refresh...")
                    if self._refresh_token():
                        continue
                    else:
                        logger.error("Token refresh failed")
                        return None

                resp.raise_for_status()
                data = resp.json()

                if data.get("error"):
                    logger.error(f"API error on {endpoint}: {data['error']}")
                    return None

                return data

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on {endpoint} (attempt {attempt + 1})")
                time.sleep(5)
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error on {endpoint}: {e}")
                if attempt < retries:
                    time.sleep(5)
                else:
                    return None

        return None

    def _refresh_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token_str:
            logger.error("No refresh token available")
            return False

        try:
            resp = self.session.post(
                f"{self.base_url}/auth/refresh_token",
                json={"refreshToken": self.refresh_token_str},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("accessToken"):
                self.access_token = data["accessToken"]
                if data.get("refreshToken"):
                    self.refresh_token_str = data["refreshToken"]
                self._update_headers()
                logger.info("Token refreshed successfully")
                return True

        except Exception as e:
            logger.error(f"Token refresh failed: {e}")

        return False

    def test_connection(self, portfolio_id: str) -> bool:
        """Verify the current access token works by hitting a lightweight endpoint.

        Returns True if authenticated and the API responds successfully,
        False otherwise (caller should then attempt a token refresh).
        """
        try:
            result = self.get_portfolio(portfolio_id)
            if result is not None:
                logger.info("Invo connection test passed")
                return True
            logger.warning("Invo connection test returned no data")
            return False
        except Exception as e:
            logger.error(f"Invo connection test failed: {e}")
            return False

    def get_portfolio(self, portfolio_id: str) -> Optional[dict]:
        """Fetch portfolio summary by ID."""
        data = self._request(
            "portfolios/get_portfolio_by_id",
            {"portfolioId": portfolio_id}
        )
        if data and data.get("success"):
            return data.get("portfolio")
        return None

    def get_investments(self, portfolio_id: str) -> Optional[list]:
        """Fetch all open investments (positions) for a portfolio."""
        data = self._request(
            "investments/get_investments",
            {
                "portfolioId": portfolio_id,
                "isOpen": True,
                "params": {
                    "page": 1,
                    "size": 50,
                },
            }
        )
        if data and data.get("success"):
            investments = []
            for key in ["investmentsTicker", "investmentsBusiness",
                        "investmentsMaterial", "investmentsProperty"]:
                items = data.get(key)
                if items:
                    investments.extend(items)
            return investments
        return None

    def get_closed_investments(self, portfolio_id: str) -> Optional[list]:
        """Fetch recently closed investments for a portfolio."""
        data = self._request(
            "investments/get_investments",
            {
                "portfolioId": portfolio_id,
                "isOpen": False,
                "params": {
                    "page": 1,
                    "size": 50,
                },
            }
        )
        if data and data.get("success"):
            investments = []
            for key in ["investmentsTicker", "investmentsBusiness",
                        "investmentsMaterial", "investmentsProperty"]:
                items = data.get(key)
                if items:
                    investments.extend(items)
            return investments
        return None

    def parse_investment(self, inv: dict) -> dict:
        """Parse a raw investment into a clean trade signal."""
        return {
            "id": inv.get("id"),
            "ticker": inv.get("ticker", "").upper(),
            "name": inv.get("name", ""),
            "direction": "LONG" if inv.get("directionLong") else "SHORT",
            "entry_price": inv.get("entryPrice"),
            "current_price": inv.get("currentPrice"),
            "take_profit": inv.get("priceTarget"),
            "stop_loss": inv.get("stopLoss"),
            "leverage": inv.get("leverage", 1),
            "position_size_pct": inv.get("positionSize", 0),
            "is_open": inv.get("isOpen", False),
            "is_current": inv.get("isCurrent", False),
            "active": inv.get("active", False),
            "created_at": inv.get("createdAt"),
            "updated_at": inv.get("updatedAt"),
            "closing_price": inv.get("closingPrice"),
            "changes": inv.get("changes", {}),
            "portfolio_id": inv.get("portfolio", {}).get("id"),
            "owner_username": inv.get("owner", {}).get("username"),
        }
