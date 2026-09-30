#!/usr/bin/env python3
"""
Capture a logged-in Z.ai session.

Opens https://chat.z.ai/auth in Playwright Firefox. Log in normally —
the script snatches the Authorization bearer token from browser traffic
(or localStorage), grabs cookies, queries /api/models, and saves
everything to .zai_credentials.json.

Logged-in credentials unlock extra models (glm-5.2, glm-5.3) beyond
glm-5.3-flash.

Usage:
    python login.py                # headed browser (default)
    python login.py --headless     # no window (you can't interact — rarely useful)
    python login.py --timeout 900  # wait up to 15 min for login
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import time

from playwright.async_api import async_playwright

AUTH_URL = "https://chat.z.ai/auth"
MODELS_URL = "https://chat.z.ai/api/models"
CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".zai_credentials.json")


def jwt_claims(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        pad = "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return {}


def is_guest(token: str) -> bool:
    claims = jwt_claims(token)
    email = str(claims.get("email") or "")
    return (not claims) or "guest" in email.lower()


async def fetch_models(request, bearer: str) -> list:
    try:
        resp = await request.get(
            MODELS_URL,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        if not resp.ok:
            print(f"[login] /api/models -> {resp.status}")
            return []
        data = await resp.json()
        items = data.get("data") if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = items.get("models") or items.get("list") or []
        ids = []
        for m in items or []:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("model") or m.get("name")
                if mid:
                    ids.append(str(mid))
            elif m:
                ids.append(str(m))
        return ids
    except Exception as e:
        print(f"[login] model list fetch failed: {e}")
        return []


async def login(timeout_s: int = 600, headless: bool = False) -> None:
    async with async_playwright() as p:
        browser = await p.firefox.launch(
            headless=headless,
            firefox_user_prefs={
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False,
            },
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0",
        )
        page = await context.new_page()

        captured = {"bearer": ""}

        def on_request(req):
            try:
                auth = req.headers.get("authorization", "")
            except Exception:
                auth = ""
            if auth.lower().startswith("bearer "):
                token = auth.split(" ", 1)[1].strip()
                if token and not is_guest(token):
                    captured["bearer"] = token

        page.on("request", on_request)

        await page.goto(AUTH_URL, wait_until="domcontentloaded", timeout=60000)
        print(f"[login] Opened {AUTH_URL}")
        print("[login] Complete login in the Firefox window (waiting for a non-guest token)...")

        token = ""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                ls_token = await page.evaluate("localStorage.getItem('token') || ''")
            except Exception:
                ls_token = ""
            if ls_token and not is_guest(ls_token):
                token = ls_token
                break
            if captured["bearer"]:
                token = captured["bearer"]
                break
            await asyncio.sleep(0.5)

        if not token:
            await browser.close()
            raise SystemExit(f"[login] Timed out after {timeout_s}s — no logged-in token seen.")

        bearer = captured["bearer"] or token
        claims = jwt_claims(bearer)
        email = str(claims.get("email") or "")
        user_id = str(claims.get("id") or claims.get("sub") or "")

        # Fresh cookie jar from the logged-in browser context
        cookies = await context.cookies("https://chat.z.ai")
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        models = await fetch_models(page.request, bearer)

        await browser.close()

    # Merge into existing credentials (keep device_id / captcha / extras)
    existing = {}
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    creds = {
        **existing,
        "token": bearer,
        "cookie": cookie_header or existing.get("cookie", ""),
        "logged_in": True,
        "email": email,
        "user_id": user_id,
        "models": models,
        "logged_in_at": int(time.time()),
    }
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, indent=2)

    print(f"[login] Logged in as {email or user_id or bearer[:24] + '...'}")
    if models:
        print(f"[login] Models from /api/models: {', '.join(models)}")
    else:
        print("[login] Model list unavailable (endpoint may require additional auth)")
    print(f"[login] Credentials saved to {CREDENTIALS_FILE}")
    print("[login] Server now unlocks glm-5.2 and glm-5.3 (restart server if it was already running)")


def main():
    ap = argparse.ArgumentParser(description="Capture logged-in Z.ai credentials")
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for login (default 600)")
    ap.add_argument("--headless", action="store_true", help="run without a browser window")
    args = ap.parse_args()
    try:
        asyncio.run(login(timeout_s=args.timeout, headless=args.headless))
    except KeyboardInterrupt:
        sys.exit("\n[login] Cancelled.")


if __name__ == "__main__":
    main()
