"""
InvoMirror Bot - Configuration
================================
Copy this file to config_local.py and fill in your credentials.
NEVER commit config_local.py to version control.
"""

# ── Invo API Configuration ──
INVO_API_BASE = "https://api.invoapp.com/v1_0"
INVO_EMAIL = "your_invo_email@example.com"
INVO_PASSWORD = "your_invo_password"

PAPER_BALANCE = 200.0

# Bearer token (extracted from browser - bot will auto-refresh)
INVO_ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiNDIzZmExOWQtMmJkNi00MzE0LWIwYzgtNDRjNTMyMGM3YjA3IiwiZXhwaXJlcyI6MTc3NTIxMzU3MC4wNzMyNTU1LCJkZXZpY2VfaWQiOiJmY2EwY2VhMi1jYjA1LTQ4NTItOGYzZC1mMjgwNTMyYWU0N2YiLCJ0cmFkaW5nX2FjY291bnQiOnsiaWQiOiI2ZjI2YWE5NS05OTgzLTQ3ZjUtYmZlNi1iNzg5NzQxYzU3NzEiLCJwcm92aWRlciI6InR1cm5rZXkiLCJvcmdhbml6YXRpb25faWQiOiI5ODQ0NTU2ZS00OTUxLTQ0NzgtODQyMS0zYzhkMzU3OGRmYWEiLCJ3YWxsZXRfYWRkcmVzcyI6IjB4ZTc4MjQ1MDYwRjE0MEUxMkZlRTcyMDgwOTc2OUI3MUU5M2JDMTY2NCIsInByaW1hcnkiOnRydWUsIndhbGxldCI6eyJldm1fYWRkcmVzcyI6IjB4ZTc4MjQ1MDYwRjE0MEUxMkZlRTcyMDgwOTc2OUI3MUU5M2JDMTY2NCIsInN2bV9hZGRyZXNzIjoiNHllOHI4bWtaVUhyb1hRaGZ5WDMzdUM2Y1l2OXgyeXVoTUU3enY1SkZ3U04ifX19.zFGA08eXJvRZoawCXonew_Jr-W4vnVhIUkYAF7W7_e0"
INVO_REFRESH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiNDIzZmExOWQtMmJkNi00MzE0LWIwYzgtNDRjNTMyMGM3YjA3IiwiZXhwaXJlcyI6MTgwNjc1MTQzMC42NzYyMDUyLCJkZXZpY2VfaWQiOiJmY2EwY2VhMi1jYjA1LTQ4NTItOGYzZC1mMjgwNTMyYWU0N2YiLCJ0cmFkaW5nX2FjY291bnQiOnsiaWQiOiI2ZjI2YWE5NS05OTgzLTQ3ZjUtYmZlNi1iNzg5NzQxYzU3NzEiLCJwcm92aWRlciI6InR1cm5rZXkiLCJvcmdhbml6YXRpb25faWQiOiI5ODQ0NTU2ZS00OTUxLTQ0NzgtODQyMS0zYzhkMzU3OGRmYWEiLCJ3YWxsZXRfYWRkcmVzcyI6IjB4ZTc4MjQ1MDYwRjE0MEUxMkZlRTcyMDgwOTc2OUI3MUU5M2JDMTY2NCIsInByaW1hcnkiOnRydWUsIndhbGxldCI6eyJldm1fYWRkcmVzcyI6IjB4ZTc4MjQ1MDYwRjE0MEUxMkZlRTcyMDgwOTc2OUI3MUU5M2JDMTY2NCIsInN2bV9hZGRyZXNzIjoiNHllOHI4bWtaVUhyb1hRaGZ5WDMzdUM2Y1l2OXgyeXVoTUU3enY1SkZ3U04ifX0sInJlZnJlc2hhYmxlIjp0cnVlfQ.fEumdtLpPbf-A4AgORpZ-HM3arFwqFZ_25f-iOUW6vQ"

# Portfolio IDs to monitor (from the URL: app.invoapp.com/portfolio/<ID>)
# Add as many traders as you want to follow
WATCHED_PORTFOLIOS = [
    {
        "id": "6053206f-bd17-4fda-ae27-9cf318aa9a2a",  # vortex_legion
        "name": "Vanta Protocol",
        "enabled": True,
    },
    {
        "id": "add098a5-636a-4686-b2e5-2783c6a4736f",  # @ironside
        "name": "Scalps | Hit & Dip",
        "enabled": True,
    },
    {
        "id": "5336b76e-2017-4b2b-beab-3f3fe771baf0",  # @ironside
        "name": "Swings",
        "enabled": True,
    },
    {
        "id": "7f170ba1-daa4-49e8-8864-f712a72fe457",  # @dog
        "name": "Main Portfolio",
        "enabled": True,
    },
    {
        "id": "3952d194-22fe-418b-92bd-eb2a697b31ba",  # @nur72
        "name": "BTC Only",
        "enabled": True,
    },
    # Add more portfolios here:
    # {
    #     "id": "another-portfolio-id-here",
    #     "name": "Trader Name",
    #     "enabled": True,
    # },
]

# ── Binance API Configuration ──
BINANCE_API_KEY = "uX4ySFCwA1iem34hcP8l1kQibkbvaL7IXczYedkOmqYazTYHAIkusEKxGzNAA1s1"
BINANCE_API_SECRET = "ZanES0ZbuwT8Rlg6OgxgJhEgo9ILEFah2Q0YAcEyMzbj36fGPyNCjhVetV1Wm1dO"

# ── Trading Configuration ──
# How much of your available balance to use per trade (as a decimal)
# 0.05 = 5% of available balance per trade
TRADE_ALLOCATION_PCT = 0.05

# Maximum amount in USDT to spend on a single trade
MAX_TRADE_AMOUNT_USDT = 50.0

# Minimum amount in USDT for a trade (Binance minimums)
MIN_TRADE_AMOUNT_USDT = 10.0

# Only mirror LONG/BUY trades (True for Australian spot-only)
LONG_ONLY = True

# Ignore leverage from Invo (we trade spot on Binance)
IGNORE_LEVERAGE = True

# ── Stop-Loss Configuration ──
# Auto-sell if a position drops this % from buy price
# 0.15 = sell if price drops 15% below what you paid
STOP_LOSS_PCT = 0.15

# ── Polling Configuration ──
# How often to check for new trades (seconds)
POLL_INTERVAL = 15

# How often to check for closed positions (seconds)
CLOSE_CHECK_INTERVAL = 120

# ── Risk Management ──
# Maximum number of simultaneous open positions
MAX_OPEN_POSITIONS = 8

# Stop trading if total portfolio drops below this % of starting balance
CIRCUIT_BREAKER_PCT = 0.70  # Stop at 30% loss

# ── Operational Mode ──
# "paper" = log trades but don't execute on Binance
# "live" = actually execute trades on Binance
MODE = "live"

# ── Logging ──
LOG_FILE = "invo_mirror.log"
LOG_LEVEL = "INFO"

# ── Notifications (optional) ──
# Set to None to disable
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None
