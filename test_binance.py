import time, hmac, hashlib, requests

# Paste your keys here
API_KEY = "uX4ySFCwA1iem34hcP8l1kQibkbvaL7IXczYedkOmqYazTYHAIkusEKxGzNAA1s1"
API_SECRET = "ZanES0ZbuwT8Rlg6OgxgJhEgo9ILEFah2Q0YAcEyMzbj36fGPyNCjhVetV1Wm1dO"

# Sync time
r = requests.get("https://api.binance.com/api/v3/time")
server_time = r.json()["serverTime"]
local_time = int(time.time() * 1000)
offset = server_time - local_time
print(f"Time offset: {offset}ms")

# Build signed request
ts = int(time.time() * 1000) + offset
params = f"timestamp={ts}&recvWindow=10000"
sig = hmac.new(API_SECRET.encode(), params.encode(), hashlib.sha256).hexdigest()

# Call account endpoint
r2 = requests.get(
    f"https://api.binance.com/api/v3/account?{params}&signature={sig}",
    headers={"X-MBX-APIKEY": API_KEY},
)
print(f"Status: {r2.status_code}")
print(r2.text[:500])