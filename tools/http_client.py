"""Shared HTTP client. api-sports.io base config + SSL bypass for Windows dev."""
import os, requests, urllib3
from dotenv import load_dotenv

load_dotenv()

_DEV = os.getenv("ENV", "development") == "development"

# api-sports.io (primary — national team history, corners, cards)
AS_KEY = os.getenv("API_FOOTBALL_KEY", "")
AS_BASE = "https://v3.football.api-sports.io"
AS_HEADERS = {"x-apisports-key": AS_KEY}

# football-data.org v4 (fallback — fixtures, lineups)
FD_KEY = os.getenv("FOOTBALL_DATA_KEY", "")
FD_BASE = "https://api.football-data.org/v4"
FD_HEADERS = {"X-Auth-Token": FD_KEY}

if _DEV:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get(url: str, **kwargs) -> requests.Response:
    if _DEV:
        kwargs.setdefault("verify", False)
    return requests.get(url, **kwargs)


def fd_get(endpoint: str, params: dict = None, _retries: int = 3) -> dict:
    """GET from football-data.org v4. Retries on SSL/connection errors with
    backoff, trying verify=False on each pass (Hetzner TLS quirk)."""
    import requests as _req, time as _time
    url = f"{FD_BASE}/{endpoint.lstrip('/')}"
    last_err = None
    for attempt in range(_retries):
        for verify in (True, False):
            try:
                if not verify:
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = get(url, headers=FD_HEADERS, params=params or {}, timeout=15, verify=verify)
                r.raise_for_status()
                return r.json()
            except _req.exceptions.RequestException as e:
                last_err = e
        if attempt < _retries - 1:
            _time.sleep(5 * (attempt + 1))
    raise last_err


def as_get(endpoint: str, params: dict = None, _retry: int = 2) -> dict:
    """GET from api-sports.io v3. Auto-retries on 429 with 70s backoff."""
    import time
    url = f"{AS_BASE}/{endpoint.lstrip('/')}"
    r = get(url, headers=AS_HEADERS, params=params or {}, timeout=15)
    if r.status_code == 429 and _retry > 0:
        print(f"  429 rate limit — sleeping 70s then retry ({_retry} left)...")
        time.sleep(70)
        return as_get(endpoint, params, _retry - 1)
    r.raise_for_status()
    return r.json()
