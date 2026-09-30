#!/usr/bin/env python3
"""
Capture a logged-in Z.ai session.

Opens https://chat.z.ai/auth in Playwright Firefox. Log in normally —
the script snatches the Authorization bearer token from browser traffic
(or localStorage), grabs cookies, queries /api/models, and saves
everything to .zai_credentials.json.

The browser session (cookies + localStorage) is remembered in
.zai_browser_state.json, so reruns restore it and skip login while the
token stays valid. client.py reuses the same file.

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
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".zai_browser_state.json")


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


async def fetch_models(request, bearer: str) -> tuple[int, list]:
    """Returns (status, model_ids). status 0 = network failure."""
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
            return resp.status, []
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
        return resp.status, ids
    except Exception as e:
        print(f"[login] model list fetch failed: {e}")
        return 0, []


async def login(timeout_s: int = 600, headless: bool = False) -> None:
    async with async_playwright() as p:
        browser = await p.firefox.launch(
            headless=headless,
            firefox_user_prefs={
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False,
            },
        )
        # Restore remembered browser session if we have one
        restore_state = STATE_FILE if os.path.exists(STATE_FILE) else None
        if restore_state:
            print(f"[login] Restoring remembered session from {os.path.basename(STATE_FILE)}")
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0",
            storage_state=restore_state,
        )
        page = await context.new_page()

        captured = {"bearer": ""}
        rejected: set[str] = set()

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
        print("[login] Waiting for a valid logged-in token (log in if the page asks)...")

        token = ""
        models: list = []
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                ls_token = await page.evaluate("localStorage.getItem('token') || ''")
            except Exception:
                ls_token = ""
            # Prefer fresh localStorage token, fall back to sniffed traffic
            if ls_token and not is_guest(ls_token):
                candidate = ls_token
            elif captured["bearer"]:
                candidate = captured["bearer"]
            else:
                await asyncio.sleep(0.5)
                continue

            if candidate in rejected:
                await asyncio.sleep(0.5)
                continue

            # Validate against /api/models — expired sessions come back 401/403
            status, model_ids = await fetch_models(page.request, candidate)
            if status in (401, 403):
                print(f"[login] Stored session expired ({status}) — log in again in the window")
                rejected.add(candidate)
                captured["bearer"] = ""
                try:
                    await page.evaluate("localStorage.removeItem('token')")
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                continue

            token = candidate
            models = model_ids
            break

        if not token:
            await browser.close()
            raise SystemExit(f"[login] Timed out after {timeout_s}s — no valid logged-in token seen.")

        claims = jwt_claims(token)
        email = str(claims.get("email") or "")
        user_id = str(claims.get("id") or claims.get("sub") or "")

        # Fresh cookie jar from the logged-in browser context
        cookies = await context.cookies("https://chat.z.ai")
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        # Remember this browser session so reruns skip the login
        try:
            await context.storage_state(path=STATE_FILE)
            print(f"[login] Browser session remembered in {os.path.basename(STATE_FILE)}")
        except OSError as e:
            print(f"[login] Could not save browser state: {e}")

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
        "token": token,
        "cookie": cookie_header or existing.get("cookie", ""),
        "logged_in": True,
        "email": email,
        "user_id": user_id,
        "models": models,
        "logged_in_at": int(time.time()),
    }
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, indent=2)

    print(f"[login] Logged in as {email or user_id or token[:24] + '...'}")
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
