#!/usr/bin/env python3
"""
Z.ai Browser Client (async)

Uses Playwright async API with SSE response interception.
For initial setup and interactive chat via browser.
"""

import asyncio
import time
import random
from typing import Optional, List
from dataclasses import dataclass
from playwright.async_api import async_playwright, Page, Browser, BrowserContext


@dataclass
class ChatMessage:
    role: str
    content: str


class ZaiClient:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._playwright = None

    async def start(self) -> None:
        await self.close()
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.firefox.launch(
            headless=self.headless,
            firefox_user_prefs={
                "general.useragent.override": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False,
            }
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
        )
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
        """)
        self.page = await self.context.new_page()
        await self.page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_selector("#app", state="attached", timeout=60000)
        await self.page.wait_for_timeout(5000)
        # Inject AFTER page scripts have loaded and set their own fetch
        await self._inject_sse_interceptor()

    async def _inject_sse_interceptor(self) -> None:
        """Inject SSE interceptor AFTER page JS so ours is the final fetch."""
        await self.page.evaluate("""
            (() => {
                window.__zai_sse = { thinking: '', answer: '', done: false };
                const _origFetch = window.fetch;
                window.fetch = async function(...args) {
                    const resp = await _origFetch.apply(this, args);
                    const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
                    if (!url.includes('/api/v2/chat/completions')) return resp;
                    const ct = resp.headers.get('content-type') || '';
                    if (!ct.includes('text/event-stream')) return resp;

                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';
                    let thinking = '';
                    let answer = '';
                    window.__zai_sse = { thinking: '', answer: '', done: false };

                    const stream = new ReadableStream({
                        start(controller) {
                            (function pump() {
                                reader.read().then(({ done, value }) => {
                                    if (done) { window.__zai_sse.done = true; controller.close(); return; }
                                    buffer += decoder.decode(value, { stream: true });
                                    const lines = buffer.split('\\n');
                                    buffer = lines.pop();
                                    for (const line of lines) {
                                        if (!line.startsWith('data: ')) continue;
                                        const j = line.slice(6).trim();
                                        if (!j || j === '[DONE]') { window.__zai_sse.done = true; continue; }
                                        try {
                                            const o = JSON.parse(j);
                                            if (o.type === 'chat:completion' && o.data) {
                                                const d = o.data.delta_content || '';
                                                if (o.data.phase === 'thinking') thinking += d;
                                                else answer += d;
                                            }
                                        } catch(e) {}
                                    }
                                    window.__zai_sse.thinking = thinking;
                                    window.__zai_sse.answer = answer;
                                    controller.enqueue(value);
                                    pump();
                                }).catch(() => { window.__zai_sse.done = true; try { controller.close(); } catch(e) {} });
                            })();
                        }
                    });
                    return new Response(stream, { status: resp.status, statusText: resp.statusText, headers: resp.headers });
                };
            })();
        """)

    async def wait_for_auth(self) -> Optional[str]:
        await asyncio.to_thread(input, "Complete captcha/login, then press ENTER...")
        token = await self.page.evaluate("localStorage.getItem('token')")
        return token

    async def _reset_sse(self) -> None:
        await self.page.evaluate("window.__zai_sse = { thinking: '', answer: '', done: false }")

    async def _type_and_send(self, message: str) -> None:
        textarea = await self._find_element([
            '#chat-input', 'textarea[id="chat-input"]', 'textarea',
            'textarea[placeholder]', 'div textarea', '[contenteditable="true"]'
        ])
        if not textarea:
            raise RuntimeError("Could not find message input textarea")

        try:
            await textarea.fill("")
            await textarea.fill(message)
        except Exception:
            await self.page.evaluate("""
                (msg) => {
                    const el = document.querySelector('#chat-input')
                        || document.querySelector('textarea')
                        || document.querySelector('[contenteditable="true"]');
                    if (!el) return false;
                    el.focus();
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    setter.call(el, msg);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
            """, message)

        await self.page.wait_for_timeout(200 + random.randint(0, 100))

        send_btn = await self.page.query_selector('#send-message-button')
        if send_btn:
            await self.page.evaluate("() => { const b = document.querySelector('#send-message-button'); if(b) b.disabled=false; }")
            await self.page.wait_for_timeout(100 + random.randint(0, 100))
            try:
                await send_btn.click()
                return
            except Exception:
                pass

        await textarea.press("Enter")

    async def _poll_sse(self, timeout: int = 120) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            sse = await self.page.evaluate("window.__zai_sse || {}")
            if sse.get("done"):
                return sse
            await asyncio.sleep(0.05)
        return await self.page.evaluate("window.__zai_sse || {}")

    async def send_message(self, message: str, timeout: int = 120) -> str:
        if not self.page:
            raise RuntimeError("Client not started. Call start() first.")
        await self._reset_sse()
        await self._type_and_send(message)
        sse = await self._poll_sse(timeout)
        return sse.get("answer", "") or await self._extract_last_assistant_message_from_dom()

    async def send_message_stream(self, message: str, timeout: int = 120, include_thinking: bool = False):
        """
        Send message and yield response chunks as they arrive.

        Yields answer text deltas. If include_thinking is True, also yields
        thinking deltas (phase-prefixed strings "thinking:" / "answer:").
        """
        if not self.page:
            raise RuntimeError("Client not started. Call start() first.")
        await self._reset_sse()
        await self._type_and_send(message)
        answer_len = 0
        thinking_len = 0
        start = time.time()
        answer = ""
        thinking = ""
        while time.time() - start < timeout:
            sse = await self.page.evaluate("window.__zai_sse || {}")
            answer = sse.get("answer", "")
            thinking = sse.get("thinking", "")
            done = sse.get("done", False)

            if include_thinking and len(thinking) > thinking_len:
                yield "thinking:" + thinking[thinking_len:]
                thinking_len = len(thinking)

            if len(answer) > answer_len:
                chunk = answer[answer_len:]
                yield ("answer:" + chunk) if include_thinking else chunk
                answer_len = len(answer)

            if done:
                # flush any trailing buffered text
                if include_thinking and len(thinking) > thinking_len:
                    yield "thinking:" + thinking[thinking_len:]
                if len(answer) > answer_len:
                    chunk = answer[answer_len:]
                    yield ("answer:" + chunk) if include_thinking else chunk
                return
            await asyncio.sleep(0.03)

        if answer_len == 0:
            dom = await self._extract_last_assistant_message_from_dom()
            if dom:
                yield ("answer:" + dom) if include_thinking else dom
        elif len(answer) > answer_len or (include_thinking and len(thinking) > thinking_len):
            if include_thinking and len(thinking) > thinking_len:
                yield "thinking:" + thinking[thinking_len:]
            if len(answer) > answer_len:
                chunk = answer[answer_len:]
                yield ("answer:" + chunk) if include_thinking else chunk

    async def send_message_stream_full(self, message: str, timeout: int = 120):
        """
        Stream thinking and answer as (phase, delta) tuples.
        phase is 'thinking' or 'answer'.
        """
        async for item in self.send_message_stream(message, timeout=timeout, include_thinking=True):
            phase, _, delta = item.partition(":")
            yield phase, delta

    async def send_message_full(self, message: str, timeout: int = 120) -> dict:
        """Send message and return both thinking and response."""
        if not self.page:
            raise RuntimeError("Client not started. Call start() first.")
        await self._reset_sse()
        await self._type_and_send(message)
        sse = await self._poll_sse(timeout)
        return {"thinking": sse.get("thinking", ""), "response": sse.get("answer", "")}

    async def get_chat_history(self) -> List[ChatMessage]:
        try:
            result = await self.page.evaluate("""
                () => {
                    const msgs = [];
                    function clean(el) {
                        const c = el.cloneNode(true);
                        c.querySelectorAll('style, script, noscript, svg').forEach(s => s.remove());
                        c.querySelectorAll('.thinking-chain-container, .thinking-block').forEach(t => t.remove());
                        return (c.innerText || '').trim();
                    }
                    document.querySelectorAll('.chat-user .markdown-prose').forEach(el => {
                        const t = clean(el);
                        if (t) msgs.push({role:'user', content:t});
                    });
                    document.querySelectorAll('.chat-assistant .markdown-prose').forEach(el => {
                        const t = clean(el);
                        if (t) msgs.push({role:'assistant', content:t});
                    });
                    return msgs;
                }
            """)
            return [ChatMessage(m['role'], m['content']) for m in result]
        except Exception:
            return []

    async def _find_element(self, selectors: List[str], timeout: int = 3000):
        per_selector = max(timeout // len(selectors), 300)
        for sel in selectors:
            try:
                el = await self.page.wait_for_selector(sel, timeout=per_selector)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None

    async def _extract_last_assistant_message_from_dom(self) -> str:
        try:
            return await self.page.evaluate("""
                () => {
                    function clean(el) {
                        const c = el.cloneNode(true);
                        c.querySelectorAll('style, script, noscript, svg').forEach(s => s.remove());
                        c.querySelectorAll('.thinking-chain-container, .thinking-block').forEach(t => t.remove());
                        return (c.innerText || '').trim();
                    }
                    const b = document.querySelectorAll('.chat-assistant .markdown-prose');
                    return b.length ? clean(b[b.length-1]) : '';
                }
            """)
        except Exception:
            return ""

    async def close(self):
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        finally:
            self.browser = None
            self.context = None
            self.page = None
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()


async def main():
    """Interactive chat with Z.ai"""
    client = ZaiClient(headless=True)

    try:
        await client.start()
        await client.wait_for_auth()

        print("\n" + "=" * 60)
        print("CHAT STARTED - Type 'quit' to exit")
        print("=" * 60)

        while True:
            try:
                user_input = (await asyncio.to_thread(input, "\nYou: ")).strip()
                if not user_input:
                    continue
                if user_input.lower() in ('quit', 'exit', 'q'):
                    break

                # Live-stream the reply (and thinking, if any)
                print("Assistant: ", end="", flush=True)
                thinking_buf = ""
                answer_started = False
                async for phase, delta in client.send_message_stream_full(user_input):
                    if not delta:
                        continue
                    if phase == "thinking":
                        thinking_buf += delta
                        print(f"\r[thinking] {thinking_buf}", end="", flush=True)
                    else:
                        if not answer_started:
                            answer_started = True
                            print(f"\r{' ' * 78}\r", end="")
                        print(delta, end="", flush=True)
                if thinking_buf and not answer_started:
                    print()
                print()

            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"\n[Error] {e}")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
