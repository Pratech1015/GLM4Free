#!/usr/bin/env python3
"""
Z.ai API Client

Pure HTTP client - no browser needed after initial token extraction.
Replicates browser-level requests including HMAC-SHA256 signature.
"""

import json
import time
import uuid
import os
import hmac
import hashlib
import base64
from typing import Optional, AsyncGenerator
from datetime import datetime, timezone
import aiohttp

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), ".zai_credentials.json")

# From zai_bundle.js sne(): first HMAC key literal
_SIG_SECRET = b"key-@@@@)))()((9))-xxxx&&&%%%%%"


def _compute_signature(sorted_payload: str, prompt: str, timestamp: str) -> str:
    """Replicate the browser's HMAC-SHA256 signature (X-Signature header)."""
    ts_num = int(timestamp)

    encoded = prompt.encode("utf-8")
    b64_prompt = base64.b64encode(encoded).decode("ascii")

    # Signing input: sortedPayload|base64(prompt)|timestamp
    message = f"{sorted_payload}|{b64_prompt}|{timestamp}"

    # Time bucket: 5-minute intervals (string, as in JS ""+m)
    time_bucket = str(ts_num // (5 * 60 * 1000))

    # Intermediate: HMAC-SHA256(key=SIG_SECRET, msg=time_bucket) as hex
    intermediate = hmac.new(
        _SIG_SECRET,
        time_bucket.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Final: HMAC-SHA256(key=intermediate, msg=message) as hex
    signature = hmac.new(
        intermediate.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return signature


def _build_sorted_payload(timestamp: str, request_id: str, user_id: str) -> str:
    """Build the sorted payload from core fields (alphabetically sorted)."""
    fields = {
        "requestId": request_id,
        "timestamp": timestamp,
        "user_id": user_id,
    }
    return ",".join(f"{k},{v}" for k, v in sorted(fields.items()))


def _build_url_params(
    timestamp: str,
    request_id: str,
    user_id: str,
    token: str,
    chat_id: str,
    current_url: str,
    pathname: str,
) -> str:
    """Build all URL query parameters matching the browser's fingerprinting."""
    from urllib.parse import quote

    now = datetime.now(timezone.utc)
    params = [
        ("timestamp", timestamp),
        ("requestId", request_id),
        ("user_id", user_id),
        ("version", "0.0.1"),
        ("platform", "web"),
        ("token", token),
        ("user_agent", "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0"),
        ("language", "en-US"),
        ("languages", "en-US"),
        ("timezone", "Asia/Kolkata"),
        ("cookie_enabled", "true"),
        ("screen_width", "1366"),
        ("screen_height", "768"),
        ("screen_resolution", "1366x768"),
        ("viewport_height", "1080"),
        ("viewport_width", "1920"),
        ("viewport_size", "1920x1080"),
        ("color_depth", "24"),
        ("pixel_ratio", "1"),
        ("current_url", current_url),
        ("pathname", pathname),
        ("search", ""),
        ("hash", ""),
        ("host", "chat.z.ai"),
        ("hostname", "chat.z.ai"),
        ("protocol", "https:"),
        ("referrer", ""),
        ("title", "Z.ai - Advanced AI Chatbot & Agent powered by GLM-5.3-Flash"),
        ("timezone_offset", "-330"),
        ("local_time", now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"),
        ("utc_time", now.strftime("%a, %d %b %Y %H:%M:%S GMT")),
        ("is_mobile", "false"),
        ("is_touch", "false"),
        ("max_touch_points", "0"),
        ("browser_name", "Firefox"),
        ("os_name", "Linux"),
        ("signature_timestamp", timestamp),
    ]
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)


class ZaiApiClient:
    BASE_URL = "https://chat.z.ai"

    def __init__(
        self,
        token: str,
        captcha_verify_param: str = "",
        device_id: str = "",
        cookie: str = "",
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.token = token
        self.captcha_verify_param = captcha_verify_param
        self.device_id = device_id
        self.cookie = cookie
        self._session = session
        self._own_session = session is None
        self._user_id, self._guest_name = self._parse_token(token)
        self._chat_id: Optional[str] = None
        self._last_msg_id: Optional[str] = None
        self._history: list = []
        self._force_inline_history: bool = False

    @staticmethod
    def _parse_token(token: str) -> tuple:
        """Extract user id and guest name from JWT payload."""
        try:
            parts = token.split(".")
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded))
            user_id = data.get("id", "") or str(uuid.uuid4())
            email = data.get("email", "")
            guest_name = f"Guest-{email.split('-')[1].split('@')[0]}" if email.startswith("guest-") else "Guest"
            return user_id, guest_name
        except Exception:
            return str(uuid.uuid4()), "Guest"

    @classmethod
    def from_saved_credentials(cls, credentials_path: str = CREDENTIALS_FILE) -> Optional["ZaiApiClient"]:
        if not os.path.exists(credentials_path):
            return None
        try:
            with open(credentials_path) as f:
                creds = json.load(f)
            token = creds.get("token", "")
            if not token:
                return None
            return cls(
                token=token,
                captcha_verify_param=creds.get("captcha_verify_param", ""),
                device_id=creds.get("device_id", ""),
                cookie=creds.get("cookie", ""),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    @classmethod
    def from_env(cls) -> Optional["ZaiApiClient"]:
        token = os.environ.get("ZAI_TOKEN", "")
        if not token:
            return None
        return cls(
            token=token,
            captcha_verify_param=os.environ.get("ZAI_CAPTCHA_PARAM", ""),
            device_id=os.environ.get("ZAI_DEVICE_ID", ""),
            cookie=os.environ.get("ZAI_COOKIE", ""),
        )

    @classmethod
    async def setup(cls, headless: bool = True, save: bool = True) -> "ZaiApiClient":
        """Launch browser ONCE to extract token + captcha, then close it."""
        import asyncio
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=headless)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = await context.new_page()

            captured = {"captcha": "", "requests": 0, "cookies": []}

            async def handle_request(route):
                request = route.request
                if "/api/v2/chat/completions" in request.url:
                    body = request.post_data
                    if body:
                        captured["requests"] += 1
                        try:
                            data = json.loads(body)
                            captcha = data.get("captcha_verify_param", "")
                            if captcha:
                                captured["captcha"] = captcha
                                # Save full request for comparison/debugging
                                captured["url"] = request.url
                                captured["body"] = body
                                captured["headers"] = dict(request.headers)
                                try:
                                    captured["cookies"] = await context.cookies()
                                except Exception:
                                    captured["cookies"] = []
                                # Abort so the single-use token is never consumed server-side
                                await route.abort()
                                return
                        except json.JSONDecodeError:
                            pass
                await route.continue_()

            page.on("console", lambda m: print(f"  [browser] {m.text[:300]}", flush=True)
                    if ("captcha" in m.text.lower() or "error" in m.text.lower() or m.type == "error") else None)
            page.on("pageerror", lambda e: print(f"  [pageerror] {str(e)[:160]}", flush=True))

            # Dump captcha fail payloads for diagnosis
            await page.add_init_script("""
                (() => {
                  const _str = JSON.stringify;
                  window.__capfails = [];
                  JSON.stringify = function(v) {
                    try {
                      if (v && typeof v === 'object') {
                        const s = _str(v);
                        if (s && (s.includes('error') || s.includes('Error') || s.includes('fail') || s.includes('F0') || s.includes('code'))) {
                          if (s.includes('certify') || s.includes('scene') || s.includes('captcha') || s.includes('Captcha') || s.includes('F0') || s.includes('fail')) {
                            window.__capfails.push(s.slice(0, 500));
                          }
                        }
                      }
                    } catch (e) {}
                    return _str.apply(this, arguments);
                  };
                })();
            """)

            def _interesting_url(url: str) -> bool:
                u = url.lower()
                return (
                    "captcha" in u
                    or "verify" in u
                    or "cloudauth" in u
                    or "chat/completions" in u
                    or "aliyuncs.com" in u
                )

            await page.route(lambda url: _interesting_url(url), handle_request)

            await page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_selector("#app", state="attached", timeout=30000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

            token = await page.evaluate("localStorage.getItem('token') || ''")

            async def force_send(msg: str) -> bool:
                ok = await page.evaluate(
                    """
                    (msg) => {
                      const el = document.querySelector('#chat-input')
                        || document.querySelector('textarea')
                        || document.querySelector('textarea[placeholder]');
                      if (!el) return false;
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
                      return true;
                    }
                    """,
                    msg,
                )
                if not ok:
                    return False
                await page.wait_for_timeout(300)
                try:
                    await page.evaluate(
                        "() => { const b=document.querySelector('#send-message-button'); if(b) b.disabled=false; }"
                    )
                    btn = page.locator("#send-message-button")
                    await btn.wait_for(state="visible", timeout=5000)
                    await btn.click(timeout=5000)
                    return True
                except Exception:
                    try:
                        await page.evaluate(
                            "() => { const b=document.querySelector('#send-message-button'); if(b) b.click(); }"
                        )
                        return True
                    except Exception:
                        return False

            await force_send("test")

            if not headless:
                print("\nIf a captcha slider appears, solve it in the browser window.")
                print("Waiting for capture...", flush=True)

            deadline = time.time() + (120 if not headless else 45)
            last_send = time.time()
            attempts = 1
            last_state = ""
            while time.time() < deadline and not captured["captcha"]:
                await asyncio.sleep(1)
                state = f"requests={captured['requests']}"
                if state != last_state:
                    print(f"  [{state}]", flush=True)
                    last_state = state
                if captured["requests"] == 0 and time.time() - last_send > 8:
                    attempts += 1
                    last_send = time.time()
                    ok = await force_send("test")
                    print(f"  [send attempt {attempts} -> {ok}]", flush=True)
                    try:
                        fails = await page.evaluate("() => (window.__capfails || []).slice(-5)")
                        for f in fails:
                            print(f"  [capfail] {f[:400]}", flush=True)
                    except Exception:
                        pass

            captcha_param = captured["captcha"]
            device_id = ""
            headers_saved = captured.get("headers") or {}
            device_id = (
                headers_saved.get("x-device-id")
                or headers_saved.get("X-Device-ID")
                or ""
            )
            if not device_id:
                try:
                    device_id = await page.evaluate(
                        "localStorage.getItem('_arms_uid') || ''"
                    )
                except Exception:
                    device_id = ""
            if not captured.get("cookies"):
                try:
                    captured["cookies"] = await context.cookies()
                except Exception:
                    captured["cookies"] = []
            # Prefer cookie header from the intercepted request (same session as captcha)
            cookie_header = ""
            hdrs = headers_saved or {}
            cookie_header = hdrs.get("cookie") or hdrs.get("Cookie") or ""
            browser_request = {
                "url": captured.get("url", ""),
                "body": captured.get("body", ""),
                "headers": headers_saved,
                "device_id": device_id,
                "cookie": cookie_header,
                "cookies": captured.get("cookies") or [],
            }
            await browser.close()

        if not token:
            raise RuntimeError("Could not extract auth token from browser")
        if not captcha_param:
            raise RuntimeError(
                "Could not capture captcha. Re-run with --visible and solve the captcha in the browser window."
            )

        # Save browser request template for debugging
        try:
            tpl_path = os.path.join(os.path.dirname(__file__), ".browser_request.json")
            with open(tpl_path, "w") as f:
                json.dump(browser_request, f, indent=2)
        except OSError:
            pass

        client = cls(
            token=token,
            captcha_verify_param=captcha_param,
            device_id=browser_request.get("device_id", ""),
            cookie=browser_request.get("cookie", ""),
        )
        if save:
            client.save_credentials()
        return client

    def save_credentials(self, path: str = CREDENTIALS_FILE):
        creds = {
            "token": self.token,
            "captcha_verify_param": self.captcha_verify_param,
            "device_id": self.device_id,
            "cookie": self.cookie,
        }
        with open(path, "w") as f:
            json.dump(creds, f, indent=2)

    @classmethod
    def auto_init(cls, credentials_path: str = CREDENTIALS_FILE) -> Optional["ZaiApiClient"]:
        client = cls.from_env()
        if client:
            return client
        return cls.from_saved_credentials(credentials_path)

    def new_conversation(self):
        self._chat_id = None
        self._last_msg_id = None
        self._history = []

    async def refresh_captcha(self, headless: bool = True) -> str:
        """
        Obtain a fresh single-use captcha.

        Prefers the pure-HTTP Aliyun TRACELESS path (no browser). Falls back
        to Firefox setup only if pure-HTTP fails (e.g. missing device identity
        or Node data builder).
        """
        try:
            import captcha as captcha_mod

            fresh_param = await captcha_mod.get_captcha_verify_param()
            self.captcha_verify_param = fresh_param
            # Pure-HTTP captcha does not rotate Z.ai cookies/token; keep them.
            self._chat_id = None
            self._last_msg_id = None
            self._force_inline_history = bool(self._history)
            await self.close()
            await self._ensure_session()
            return self.captcha_verify_param
        except Exception as e:
            print(f"[captcha] pure-HTTP failed ({e}); falling back to browser", flush=True)

        fresh = await type(self).setup(headless=headless, save=True)
        self.token = fresh.token or self.token
        self.captcha_verify_param = fresh.captcha_verify_param
        self.device_id = fresh.device_id or self.device_id
        self.cookie = fresh.cookie or self.cookie
        # Old chat_id is bound to the previous cookie session — drop it.
        self._chat_id = None
        self._last_msg_id = None
        # keep self._history so multi-turn context survives session refresh
        self._force_inline_history = bool(self._history)
        # rebuild session headers (token/cookie may change)
        await self.close()
        await self._ensure_session()
        return self.captcha_verify_param

    @staticmethod
    def _is_captcha_error(text: str) -> bool:
        return "FRONTEND_CAPTCHA_REQUIRED" in (text or "")

    @staticmethod
    def _is_retryable_session_error(text: str) -> bool:
        """Captcha failures, or stale chat after cookie/session refresh."""
        t = text or ""
        return "FRONTEND_CAPTCHA_REQUIRED" in t or (
            "INTERNAL_ERROR" in t and "Oops" in t
        )

    async def create_conversation(self) -> str:
        """Create a server-side chat before the first completion (required)."""
        await self._ensure_session()
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.device_id:
            headers["X-Device-ID"] = self.device_id
        body = json.dumps({"chat": {}}, separators=(",", ":"))
        async with self._session.post(
            f"{self.BASE_URL}/api/v1/chats/new",
            data=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"create chat failed {resp.status}: {text}")
            data = json.loads(text)
            chat_id = data.get("id") or (data.get("chat") or {}).get("id")
            if not chat_id:
                raise RuntimeError(f"create chat no id: {text[:300]}")
            self._chat_id = chat_id
            self._last_msg_id = None
            return chat_id

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            headers = {
                "authorization": f"Bearer {self.token}",
                "content-type": "application/json",
                "accept": "*/*",
                "accept-language": "en-US",
                "x-fe-version": "prod-fe-1.1.96",
                "x-region": "overseas",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0",
                "Origin": "https://chat.z.ai",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            }
            if self.cookie:
                headers["Cookie"] = self.cookie
            self._session = aiohttp.ClientSession(headers=headers)
            self._own_session = True

    async def close(self):
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _build_request(self, messages: list, chat_id: str) -> tuple:
        """Build signed URL and payload for a chat completion request."""
        timestamp = str(int(time.time() * 1000))
        request_id = str(uuid.uuid4())
        prompt = messages[-1]["content"] if messages else ""

        sorted_payload = _build_sorted_payload(timestamp, request_id, self._user_id)
        signature = _compute_signature(sorted_payload, prompt, timestamp)

        pathname = f"/c/{chat_id}" if chat_id else "/"
        current_url = f"https://chat.z.ai{pathname}"

        url_params = _build_url_params(
            timestamp, request_id, self._user_id,
            self.token, chat_id, current_url, pathname,
        )
        url_params += f"&signature_timestamp={timestamp}"

        url = f"{self.BASE_URL}/api/v2/chat/completions?{url_params}"

        # Browser sends local wall-clock for prompt variables
        local_now = datetime.now(timezone.utc).astimezone()
        local_str = local_now.strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "stream": True,
            "model": "x-preview-l",
            "messages": messages,
            "signature_prompt": prompt,
            "params": {},
            "extra": {},
            "features": {
                "image_generation": False,
                "web_search": False,
                "auto_web_search": False,
                "preview_mode": True,
                "flags": [],
                "vlm_tools_enable": False,
                "vlm_web_search_enable": False,
                "vlm_website_mode": False,
                "enable_thinking": True,
                "reasoning_effort": "max",
            },
            "variables": {
                "{{USER_NAME}}": self._guest_name,
                "{{USER_LOCATION}}": "Unknown",
                "{{CURRENT_DATETIME}}": local_str,
                "{{CURRENT_DATE}}": local_str.split(" ")[0],
                "{{CURRENT_TIME}}": local_str.split(" ")[1] if " " in local_str else "",
                "{{CURRENT_WEEKDAY}}": local_now.strftime("%A"),
                "{{CURRENT_TIMEZONE}}": "Asia/Kolkata",
                "{{USER_LANGUAGE}}": "en-US",
            },
            "chat_id": chat_id,
            "id": str(uuid.uuid4()),
            "current_user_message_id": str(uuid.uuid4()),
            "current_user_message_parent_id": self._last_msg_id,
            "background_tasks": {
                "title_generation": True,
                "tags_generation": True,
            },
            "captcha_verify_param": self.captcha_verify_param,
        }

        return url, payload, timestamp, signature

    async def _do_stream(self, messages: list, timeout: int = 120, chat_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        await self._ensure_session()

        if chat_id is not None:
            self._chat_id = chat_id
            self._last_msg_id = None

        if self._chat_id is None:
            await self.create_conversation()

        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            attempts += 1
            if self._chat_id is None:
                await self.create_conversation()
            # After a session refresh, rebuild the prompt so prior turns are
            # visible even if the server ignores message history on a new chat.
            msgs = messages
            if self._force_inline_history and len(messages) > 1:
                parts = []
                for m in messages:
                    role = m.get("role", "user")
                    prefix = "User" if role == "user" else "Assistant"
                    parts.append(f"{prefix}: {m.get('content', '')}")
                parts.append("Assistant:")
                msgs = [{"role": "user", "content": "\n".join(parts)}]
            url, payload, ts, signature = self._build_request(msgs, self._chat_id)
            self._last_msg_id = payload["id"]
            headers = {"X-Signature": signature}
            if self.device_id:
                headers["X-Device-ID"] = self.device_id
            if self.cookie:
                headers["Cookie"] = self.cookie

            captcha_error = False
            # Browser sends compact JSON (no spaces)
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            async with self._session.post(
                url,
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    if self._is_retryable_session_error(err_body) and attempts < max_attempts:
                        print("[api] Session/captcha error — refreshing captcha...")
                        await self.refresh_captcha()
                        captcha_error = True
                    else:
                        raise RuntimeError(f"API error {resp.status}: {err_body}")

                if not captcha_error:
                    buffer = ""
                    async for line in resp.content:
                        if captcha_error:
                            break
                        decoded = line.decode("utf-8", errors="ignore")
                        buffer += decoded
                        while "\n" in buffer:
                            if captcha_error:
                                break
                            raw_line, buffer = buffer.split("\n", 1)
                            raw_line = raw_line.strip()
                            if not raw_line.startswith("data: "):
                                continue
                            data_str = raw_line[6:]
                            if not data_str or data_str == "[DONE]":
                                if captcha_error:
                                    break
                                return
                            try:
                                obj = json.loads(data_str)
                                if obj.get("type") == "chat:completion" and "data" in obj:
                                    data = obj["data"]
                                    if "error" in data:
                                        err = data["error"]
                                        err_code = err.get("code", "") if isinstance(err, dict) else ""
                                        err_dump = json.dumps(err) if isinstance(err, (dict, list)) else str(err)
                                        if (
                                            err_code == "FRONTEND_CAPTCHA_REQUIRED"
                                            or err_code == "INTERNAL_ERROR"
                                        ) and attempts < max_attempts:
                                            print("[api] Session/captcha error in SSE — refreshing captcha...")
                                            captcha_error = True
                                            break
                                        raise RuntimeError(f"API error: {err}")
                                    delta = data.get("delta_content", "")
                                    phase = data.get("phase", "")
                                    # Only stream answer text; thinking is not part of the reply.
                                    if delta and phase != "thinking":
                                        yield delta
                            except json.JSONDecodeError:
                                continue
                    if captcha_error:
                        await self.refresh_captcha()
                        continue
                    return

            if captcha_error:
                continue

        raise RuntimeError("Captcha refresh attempts exhausted. Run 'python3 setup.py --visible'.")

    async def send_message(self, message: str, timeout: int = 120, chat_id: Optional[str] = None) -> str:
        self._history.append({"role": "user", "content": message})
        chunks = []
        async for chunk in self.send_message_stream(message, timeout, chat_id=chat_id, use_history=True):
            chunks.append(chunk)
        reply = "".join(chunks)
        if reply:
            self._history.append({"role": "assistant", "content": reply})
        return reply

    async def send_message_stream(
        self,
        message: str,
        timeout: int = 120,
        chat_id: Optional[str] = None,
        use_history: bool = True,
    ) -> AsyncGenerator[str, None]:
        if use_history and self._history and self._history[-1] == {"role": "user", "content": message}:
            messages = list(self._history)
        elif use_history:
            messages = list(self._history) + [{"role": "user", "content": message}]
        else:
            messages = [{"role": "user", "content": message}]
        async for chunk in self._do_stream(messages, timeout, chat_id=chat_id):
            yield chunk
        # caller of stream API may not append assistant; do it if stream completed
        # (send_message appends itself — detect via use_history ownership)

    async def send_messages(self, messages: list, timeout: int = 120, chat_id: Optional[str] = None) -> str:
        chunks = []
        async for chunk in self._do_stream(messages, timeout, chat_id=chat_id):
            chunks.append(chunk)
        reply = "".join(chunks)
        self._history = list(messages)
        if reply:
            self._history.append({"role": "assistant", "content": reply})
        return reply

    async def send_messages_stream(self, messages: list, timeout: int = 120, chat_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        async for chunk in self._do_stream(messages, timeout, chat_id=chat_id):
            yield chunk

    async def send_message_full(self, message: str, timeout: int = 120, chat_id: Optional[str] = None) -> dict:
        await self._ensure_session()

        if chat_id is not None:
            self._chat_id = chat_id
            self._last_msg_id = None

        if self._chat_id is None:
            await self.create_conversation()

        thinking = ""
        answer = ""
        attempts = 0
        max_attempts = 3

        while attempts < max_attempts:
            attempts += 1
            if self._chat_id is None:
                await self.create_conversation()
            url, payload, ts, signature = self._build_request([{"role": "user", "content": message}], self._chat_id)
            self._last_msg_id = payload["id"]
            thinking = ""
            answer = ""

            req_headers = {"X-Signature": signature}
            if self.device_id:
                req_headers["X-Device-ID"] = self.device_id
            if self.cookie:
                req_headers["Cookie"] = self.cookie
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            async with self._session.post(
                url,
                data=body,
                headers=req_headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    if self._is_captcha_error(err_body) and attempts < max_attempts:
                        print("[api] Captcha required/expired — refreshing captcha...")
                        await self.refresh_captcha()
                        continue
                    raise RuntimeError(f"API error {resp.status}: {err_body}")

                text = await resp.text()

                if "FRONTEND_CAPTCHA_REQUIRED" in text:
                    if attempts < max_attempts:
                        print("[api] Captcha error — refreshing captcha...")
                        await self.refresh_captcha()
                        continue
                    raise RuntimeError("Captcha refresh attempts exhausted.")

                buffer = text
                while "\n" in buffer:
                    raw_line, buffer = buffer.split("\n", 1)
                    raw_line = raw_line.strip()
                    if not raw_line.startswith("data: "):
                        continue
                    data_str = raw_line[6:]
                    if not data_str or data_str == "[DONE]":
                        return {"thinking": thinking, "response": answer}
                    try:
                        obj = json.loads(data_str)
                        if obj.get("type") == "chat:completion" and "data" in obj:
                            data = obj["data"]
                            if "error" in data:
                                raise RuntimeError(f"API error: {data['error']}")
                            d = data.get("delta_content", "")
                            phase = data.get("phase", "")
                            if phase == "thinking":
                                thinking += d
                            elif phase == "answer":
                                answer += d
                    except json.JSONDecodeError:
                        continue
                return {"thinking": thinking, "response": answer}

        raise RuntimeError("Captcha refresh attempts exhausted.")

        return {"thinking": thinking, "response": answer}

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, *args):
        await self.close()
