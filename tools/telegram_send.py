"""Send pre-match reports to Telegram. Called by cron or manually.
Usage: python telegram_send.py           # D-1 only (production)
       python telegram_send.py --preview # all upcoming matches with predictions
"""
import os, sys, time
from pathlib import Path
from dotenv import load_dotenv
import http_client as hc
from generate_report import main as get_reports

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text: str) -> bool:
    import requests, urllib3, os
    dev = os.getenv("ENV", "development") == "development"
    if dev:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    resp = requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=15, verify=not dev)
    if not resp.ok:
        print(f"Telegram error: {resp.status_code} {resp.text}")
    return resp.ok


def main():
    preview = "--preview" in sys.argv
    messages = get_reports(preview=preview)
    if not messages:
        print("No messages to send")
        return

    for i, msg in enumerate(messages):
        ok = send_message(msg)
        status = "SENT" if ok else "FAILED"
        print(f"[{i+1}/{len(messages)}] {status}")
        if i < len(messages) - 1:
            time.sleep(2)

    print("Done")


if __name__ == "__main__":
    main()
