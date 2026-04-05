# InvoMirror Bot

**Autonomous trade mirroring: Invo traders → Binance spot execution.**

Monitors your followed traders on Invo (Involio) and automatically mirrors their trades on your Binance account. Zero human intervention. Zero profit sharing.

---

## How It Works

```
Invo Trader opens position
        ↓
Bot polls Invo API (every 60s)
        ↓
Detects new position (AAVE LONG)
        ↓
Executes spot BUY on Binance
        ↓
Invo Trader closes position
        ↓
Bot detects closure
        ↓
Executes spot SELL on Binance
        ↓
Logs PnL
```

## Quick Start

### 1. Clone and install

```bash
cd invo-mirror-bot
pip install -r requirements.txt
```

### 2. Get your Invo auth token

1. Open Chrome → go to `app.invoapp.com`
2. Log in and navigate to any trader's portfolio
3. Press F12 → Network tab → Fetch/XHR filter
4. Click any `get_portfolio_by_id` request
5. In Headers tab, find `Authorization: Bearer <TOKEN>`
6. Copy the entire token string (starts with `eyJ...`)
7. Also look for `refresh_token` in the network requests

### 3. Get your Binance API keys

1. Log into Binance → Profile → API Management
2. Create new API key (label: "InvoMirror")
3. Enable ONLY: "Enable Spot & Margin Trading"
4. DISABLE withdrawals
5. Optional: restrict to your server's IP address

### 4. Get Invo portfolio IDs

The portfolio ID is in the URL when viewing a trader:
```
app.invoapp.com/portfolio/6053206f-bd17-4fda-ae27-9cf318aa9a2a
                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                          This is the portfolio ID
```

### 5. Configure the bot

Edit `config.py` and fill in:

```python
INVO_ACCESS_TOKEN = "eyJ..."           # From step 2
INVO_REFRESH_TOKEN = "your_refresh..."  # From step 2

WATCHED_PORTFOLIOS = [
    {
        "id": "6053206f-bd17-4fda-ae27-9cf318aa9a2a",
        "name": "Vortex Legion",
        "enabled": True,
    },
]

BINANCE_API_KEY = "your_key"
BINANCE_API_SECRET = "your_secret"

MODE = "paper"  # Start with paper trading!
```

### 6. Run in paper mode first

```bash
python bot.py --paper
```

This logs all trades without executing on Binance. Let it run for a few days to verify it's detecting signals correctly.

### 7. Go live

Once you're confident:

```bash
# In config.py, change:
MODE = "live"

# Then run:
python bot.py
```

---

## Configuration Reference

| Setting | Default | Description |
|---|---|---|
| `TRADE_ALLOCATION_PCT` | 0.05 | % of USDT balance per trade |
| `MAX_TRADE_AMOUNT_USDT` | 50.0 | Max USDT per single trade |
| `MIN_TRADE_AMOUNT_USDT` | 10.0 | Min USDT per trade (Binance minimum) |
| `LONG_ONLY` | True | Only mirror buy/long signals (required for AU spot) |
| `POLL_INTERVAL` | 60 | Seconds between Invo API checks |
| `MAX_OPEN_POSITIONS` | 5 | Max simultaneous positions |
| `CIRCUIT_BREAKER_PCT` | 0.70 | Stop trading at 30% portfolio loss |

---

## Deploying to a VPS (24/7 operation)

### Option A: DigitalOcean ($4-6/month)

1. Create a Droplet (Ubuntu 24.04, Basic, $4/month)
2. SSH in and setup:

```bash
sudo apt update && sudo apt install python3 python3-pip -y
git clone <your-repo> invo-mirror-bot
cd invo-mirror-bot
pip3 install -r requirements.txt
```

3. Run with systemd (auto-restart on crash):

```bash
sudo nano /etc/systemd/system/invomirror.service
```

Paste:
```ini
[Unit]
Description=InvoMirror Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/invo-mirror-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable invomirror
sudo systemctl start invomirror
sudo systemctl status invomirror

# View logs:
journalctl -u invomirror -f
```

### Option B: Run on your home PC

Just run `python bot.py` in a terminal. Downside: stops when PC sleeps.

---

## Token Refresh

The Invo JWT token expires periodically. The bot automatically uses the
`refresh_token` endpoint to get a new token. If refresh fails (e.g., after
a long outage), you'll need to manually grab a new token from your browser
and update `config.py`.

To check if your token is still valid:
```bash
python -c "
from invo_client import InvoClient
import config
client = InvoClient(config)
p = client.get_portfolio('6053206f-bd17-4fda-ae27-9cf318aa9a2a')
print('OK' if p else 'TOKEN EXPIRED')
"
```

---

## File Structure

```
invo-mirror-bot/
├── bot.py              # Main entry point and polling loop
├── config.py           # All configuration (edit this)
├── invo_client.py      # Invo API communication
├── binance_client.py   # Binance order execution
├── trade_state.py      # Position tracking and persistence
├── requirements.txt    # Python dependencies
├── trade_state.json    # Auto-generated: persisted state
└── invo_mirror.log     # Auto-generated: log file
```

---

## Important Notes

- **Start with paper mode.** Always. Let it run for at least a few days.
- **Invo traders use leverage.** The bot ignores leverage and buys spot.
  A 10x leveraged AAVE long on Invo becomes a simple AAVE spot buy on Binance.
- **Australian regulations.** Futures trading is banned for retail in AU.
  This bot only executes spot trades on Binance.
- **Token security.** Never share your Invo token or Binance API keys.
  Never enable Binance withdrawal permissions.
- **Rate limits.** Invo allows 250 requests per 5 minutes. The bot uses
  ~5 per 5 minutes, well within limits.
- **Not financial advice.** This bot mirrors other people's trades. Past
  performance doesn't guarantee future results. Only trade what you can
  afford to lose.

---

## Troubleshooting

**"Auth token expired"**
→ Grab a fresh token from your browser (F12 → Network → Headers)

**"Cannot map Invo ticker to Binance symbol"**
→ The ticker isn't in the mapping. Add it to `TICKER_TO_BINANCE` in `binance_client.py`

**"Insufficient USDT balance"**
→ Deposit more USDT to Binance or lower `MIN_TRADE_AMOUNT_USDT`

**"Max open positions reached"**
→ Increase `MAX_OPEN_POSITIONS` or wait for positions to close

**Bot stops detecting trades**
→ Check if the Invo token has expired. Check `invo_mirror.log` for errors.
