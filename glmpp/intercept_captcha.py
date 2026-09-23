#!/usr/bin/env python3
"""
Firefox live interception for Z.ai captcha verify construction.

Hooks:
1. window.fetch / XMLHttpRequest for VerifyCaptchaV3 / captcha-open-southeast
2. JSON.stringify for objects containing sceneId+certifyId+deviceToken
3. XHR request bodies

Usage:
  .venv/bin/python intercept_captcha.py          # headless (quick fail)
  .venv/bin/python intercept_captcha.py --visible # show browser, solve captcha if needed
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

OUT_DIR = Path("/tmp/zai_intercept")
OUT_DIR.mkdir(exist_ok=True)

VISIBLE = "--visible" in sys.argv
HEADLESS = not VISIBLE
TIMEOUT_MS = 45_000 if not VISIBLE else 180_000

HOOK_JS = r"""
(() => {
  window.__cap = { fetch: [], xhr: [], stringify: [], errors: [] };
  const interesting = (s) => {
    if (typeof s !== "string") s = String(s ?? "");
    return s.includes("sceneId") || s.includes("certifyId") ||
           s.includes("VerifyCaptcha") || s.includes("deviceToken") ||
           s.includes("captchaVerifyParam") || s.includes("failover") ||
           s.includes("CaptchaVerify");
  };
  const _str = JSON.stringify;
  const snap = (obj) => {
    try {
      if (obj && typeof obj === "object") {
        const o = JSON.parse(_str(obj));
        if (o && (o.sceneId || o.certifyId || o.deviceToken)) {
          window.__cap.stringify.push({
            t: Date.now(),
            o,
            stack: (new Error()).stack || ""
          });
          return o;
        }
      }
    } catch (e) {}
    return null;
  };

  // JSON.stringify hook (use _str inside snap to avoid recursion)
  JSON.stringify = function (value, replacer, space) {
    snap(value);
    return _str.apply(this, arguments);
  };

  // fetch hook
  const _fetch = window.fetch;
  window.fetch = async function (input, init) {
    try {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const body = init && init.body;
      const headers = init && init.headers;
      if (url.includes("captcha") || url.includes("Verify") || interesting(body) || interesting(url)) {
        let bodyStr = "";
        if (typeof body === "string") bodyStr = body;
        else if (body && typeof body === "object") {
          try { bodyStr = _str(body); } catch (e) { bodyStr = String(body); }
        }
        window.__cap.fetch.push({
          t: Date.now(), method: (init && init.method) || "GET",
          url, body: bodyStr, headers: headers || null
        });
      }
    } catch (e) {
      window.__cap.errors.push(String(e));
    }
    return _fetch.apply(this, arguments);
  };

  // XHR hook
  const _open = XMLHttpRequest.prototype.open;
  const _send = XMLHttpRequest.prototype.send;
  const _setHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__m = method; this.__u = url; this.__h = {};
    return _open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    if (this.__h) this.__h[k] = v;
    return _setHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      const url = String(this.__u || "");
      const bodyStr = typeof body === "string" ? body : "";
      if (url.includes("captcha") || url.includes("Verify") || interesting(bodyStr) || interesting(url)) {
        window.__cap.xhr.push({
          t: Date.now(), method: this.__m, url,
          body: bodyStr, headers: this.__h || {}
        });
      }
    } catch (e) {
      window.__cap.errors.push(String(e));
    }
    return _send.apply(this, arguments);
  };
  console.log("[hook] captcha interceptors installed");
})();
"""

SEND_JS = r"""
(msg) => {
  const el = document.querySelector('#chat-input')
    || document.querySelector('textarea')
    || document.querySelector('textarea[placeholder]')
    || document.querySelector('[contenteditable="true"]');
  if (!el) return false;
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value')
      || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
    if (setter && setter.set) {
      setter.set.call(el, msg);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    } else {
      el.focus();
      el.value = msg;
      el.dispatchEvent(new Event('input', {bubbles: true}));
    }
  } else {
    el.focus();
    el.textContent = msg;
    el.dispatchEvent(new Event('input', {bubbles: true}));
  }
  return 'ok';
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    captures: dict = {
        "fetch": [],
        "xhr": [],
        "stringify": [],
        "chat": [],
        "console": [],
    }

    async with async_playwright() as p:
        browser = await p.firefox.launch(
            headless=HEADLESS,
            firefox_user_prefs={
                "general.useragent.override": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) "
                    "Gecko/20100101 Firefox/151.0"
                ),
            },
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) "
                "Gecko/20100101 Firefox/151.0"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(45000)

        def _interesting_url(url: str) -> bool:
            u = url.lower()
            return (
                "captcha" in u
                or "verify" in u
                or "cloudauth" in u
                or "chat/completions" in u
                or "aliyuncs.com" in u
            )

        async def route_handler(route):
            request = route.request
            url = request.url
            try:
                if _interesting_url(url):
                    body = request.post_data or ""
                    captures["fetch"].append({
                        "t": time.time(),
                        "method": request.method,
                        "url": url,
                        "headers": dict(request.headers),
                        "post_data": body,
                    })
                    (OUT_DIR / f"route_{int(time.time()*1000)}.json").write_text(
                        json.dumps({
                            "method": request.method,
                            "url": url,
                            "headers": dict(request.headers),
                            "post_data": body,
                        }, indent=2)
                    )
            except Exception as e:
                captures["console"].append(f"route_err:{e}")
            await route.continue_()

        await page.route(
            lambda url: _interesting_url(url),
            route_handler,
        )

        page.on("console", lambda m: captures["console"].append(f"{m.type}:{m.text[:300]}"))
        page.on("pageerror", lambda e: captures["console"].append(f"pageerror:{e}"))

        # Install hooks as early as possible on every document
        await page.add_init_script(HOOK_JS)

        print("[*] goto https://chat.z.ai", flush=True)
        await page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_selector("#app", state="attached", timeout=30000)
        except Exception as e:
            print(f"[!] #app wait: {e}", flush=True)
        await page.wait_for_timeout(5000)

        print("[*] reinject hooks...", flush=True)
        try:
            await asyncio.wait_for(page.evaluate(HOOK_JS), timeout=8)
            print("[*] hooks ok", flush=True)
        except Exception as e:
            captures["console"].append(f"hook_reinject:{e}")
            print(f"[!] hook {e}", flush=True)

        try:
            token = await asyncio.wait_for(page.evaluate("localStorage.getItem('token') || ''"), timeout=8)
        except Exception as e:
            token = ""
            print(f"[!] token err {e}", flush=True)
        print(f"[*] token={'yes' if token else 'no'} len={len(token)}", flush=True)

        # Try to trigger captcha by sending a message
        print("[*] sending...", flush=True)
        try:
            sent = await asyncio.wait_for(page.evaluate(SEND_JS, "hi"), timeout=10)
        except Exception as e:
            sent = f"err:{e}"
            print(f"[*] send err {e}", flush=True)
        print(f"[*] send={sent}", flush=True)
        try:
            btn = page.locator("#send-message-button")
            await btn.wait_for(state="visible", timeout=8000)
            # enable if disabled (svelte sometimes keeps disabled until props update)
            await page.evaluate("() => { const b=document.querySelector('#send-message-button'); if(b) b.disabled=false; }")
            await page.wait_for_timeout(300)
            await btn.click(timeout=5000)
            print("[*] clicked send", flush=True)
        except Exception as e:
            print(f"[*] click err {e}", flush=True)

        deadline = time.time() + (TIMEOUT_MS / 1000)
        last_state = ""
        send_attempts = 0
        last_send = time.time()
        while time.time() < deadline:
            await asyncio.sleep(1)
            try:
                live = await page.evaluate(
                    "(() => { try { return JSON.stringify(window.__cap); } catch(e){ return '{}'; } })()"
                )
                data = json.loads(live or "{}")
            except Exception as e:
                captures["console"].append(f"poll:{e}")
                data = {}

            n_fetch = len(data.get("fetch", []))
            n_xhr = len(data.get("xhr", []))
            n_str = len(data.get("stringify", []))
            state = f"fetch={n_fetch} xhr={n_xhr} str={n_str} sends={send_attempts}"
            if state != last_state:
                print(f"  [{state}]", flush=True)
                last_state = state

            if data.get("fetch"):
                captures["fetch"] = data["fetch"]
            if data.get("xhr"):
                captures["xhr"] = data["xhr"]
            if data.get("stringify"):
                captures["stringify"] = data["stringify"]

            # stop early if we got a verify body with data field
            for arr in (captures["fetch"], captures["xhr"]):
                for item in arr:
                    body = item.get("body") or item.get("post_data") or ""
                    if "CaptchaVerifyParam" in body or '"data"' in body and "sceneId" in body:
                        print("[+] got verify capture", flush=True)
                        deadline = time.time()  # exit soon after

            # Also capture chat completion request bodies (captcha_verify_param)
            try:
                chat = await page.evaluate(
                    """
                    (() => {
                      return (window.__cap && window.__cap.__chat) || [];
                    })()
                    """
                )
            except Exception:
                chat = []
            if chat:
                captures["chat"] = chat

            # re-trigger send periodically if no requests
            if n_fetch + n_xhr < 2 and time.time() - last_send > 15:
                send_attempts += 1
                last_send = time.time()
                sent = await page.evaluate(SEND_JS, "test")
                print(f"  [resend {send_attempts} -> {sent}]", flush=True)
                if not VISIBLE and send_attempts >= 3:
                    # headless likely needs manual solve; keep a bit longer
                    pass

        # Persist everything
        out = {
            "fetch": captures["fetch"],
            "xhr": captures["xhr"],
            "stringify": captures["stringify"][:50],
            "chat": captures["chat"],
            "console": captures["console"][-200:],
        }
        (OUT_DIR / "capture.json").write_text(json.dumps(out, indent=2))
        print(f"[+] wrote {OUT_DIR/'capture.json'}", flush=True)
        print(
            f"    fetch={len(captures['fetch'])} xhr={len(captures['xhr'])} "
            f"str={len(captures['stringify'])}",
            flush=True,
        )

        # If visible, give extra time for user to solve
        if VISIBLE:
            print("[*] --visible: waiting extra 60s for manual captcha if any...", flush=True)
            await page.wait_for_timeout(60_000)
            try:
                live = await page.evaluate("JSON.stringify(window.__cap)")
                data = json.loads(live or "{}")
                (OUT_DIR / "capture_visible_extra.json").write_text(json.dumps(data, indent=2))
            except Exception:
                pass

        await browser.close()

    # summarize verify-ish hits
    hits = 0
    for item in captures["fetch"] + captures["xhr"]:
        body = item.get("body") or item.get("post_data") or ""
        url = item.get("url") or ""
        if "Verify" in url or "CaptchaVerify" in body or "sceneId" in body:
            hits += 1
            print("[hit]", item.get("method"), url, "body_len=", len(body))
    print(f"[*] verify-ish hits: {hits}")
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
