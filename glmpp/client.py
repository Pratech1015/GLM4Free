#!/usr/bin/env python3
"""
Z.ai Browser Client

Uses Playwright for browser automation with SSE response interception.
For initial setup and interactive chat via browser.
"""

import json
import time
import random
from typing import Optional, List
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext


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

    def start(self) -> None:
        self.close()
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.firefox.launch(
            headless=self.headless,
            firefox_user_prefs={
                "general.useragent.override": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False,
            }
        )
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
        )
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
        """)
        self.page = self.context.new_page()
        self.page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_selector("#app", state="attached", timeout=60000)
        self.page.wait_for_timeout(5000)
        # Inject AFTER page scripts have loaded and set their own fetch
        self._inject_sse_interceptor()

    def _inject_sse_interceptor(self) -> None:
        """Inject SSE interceptor AFTER page JS so ours is the final fetch."""
        self.page.evaluate("""
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

    def wait_for_auth(self) -> Optional[str]:
        input("Complete captcha/login, then press ENTER...")
        token = self.page.evaluate("localStorage.getItem('token')")
        return token

    def _reset_sse(self) -> None:
        self.page.evaluate("window.__zai_sse = { thinking: '', answer: '', done: false }")

    def _type_and_send(self, message: str) -> None:
        textarea = self._find_element([
            '#chat-input', 'textarea[id="chat-input"]', 'textarea',
            'textarea[placeholder]', 'div textarea', '[contenteditable="true"]'
        ])
        if not textarea:
            raise RuntimeError("Could not find message input textarea")

        try:
            textarea.fill("")
            textarea.fill(message)
        except Exception:
            self.page.evaluate("""
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

        self.page.wait_for_timeout(200 + random.randint(0, 100))

        send_btn = self.page.query_selector('#send-message-button')
        if send_btn:
            self.page.evaluate("() => { const b = document.querySelector('#send-message-button'); if(b) b.disabled=false; }")
            self.page.wait_for_timeout(100 + random.randint(0, 100))
            try:
                send_btn.click()
                return
            except Exception:
                pass

        textarea.press("Enter")

    def _poll_sse(self, timeout: int = 120) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            sse = self.page.evaluate("window.__zai_sse || {}")
            if sse.get("done"):
                return sse
            time.sleep(0.05)
        return self.page.evaluate("window.__zai_sse || {}")

    def send_message(self, message: str, timeout: int = 120) -> str:
        if not self.page:
            raise RuntimeError("Client not started. Call start() first.")
        self._reset_sse()
        self._type_and_send(message)
        sse = self._poll_sse(timeout)
        return sse.get("answer", "") or self._extract_last_assistant_message_from_dom()

    def send_message_stream(self, message: str, timeout: int = 120):
        """Send message and yield response chunks as they arrive."""
        if not self.page:
            raise RuntimeError("Client not started. Call start() first.")
        self._reset_sse()
        self._type_and_send(message)
        answer_len = 0
        start = time.time()
        while time.time() - start < timeout:
            sse = self.page.evaluate("window.__zai_sse || {}")
            answer = sse.get("answer", "")
            done = sse.get("done", False)
            if len(answer) > answer_len:
                yield answer[answer_len:]
                answer_len = len(answer)
            if done:
                return
            time.sleep(0.05)
        if answer_len == 0:
            dom = self._extract_last_assistant_message_from_dom()
            if dom:
                yield dom

    def send_message_full(self, message: str, timeout: int = 120) -> dict:
        """Send message and return both thinking and response."""
        if not self.page:
            raise RuntimeError("Client not started. Call start() first.")
        self._reset_sse()
        self._type_and_send(message)
        sse = self._poll_sse(timeout)
        return {"thinking": sse.get("thinking", ""), "response": sse.get("answer", "")}

    def get_chat_history(self) -> List[ChatMessage]:
        try:
            result = self.page.evaluate("""
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

    def _find_element(self, selectors: List[str], timeout: int = 3000):
        per_selector = max(timeout // len(selectors), 300)
        for sel in selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=per_selector)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
        return None

    def _extract_last_assistant_message_from_dom(self) -> str:
        try:
            return self.page.evaluate("""
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

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        finally:
            self.browser = None
            self.context = None
            self.page = None
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.close()


def main():
    """Interactive chat with Z.ai"""
    client = ZaiClient(headless=True)

    try:
        client.start()
        client.wait_for_auth()

        print("\n" + "=" * 60)
        print("CHAT STARTED - Type 'quit' to exit")
        print("=" * 60)

        while True:
            try:
                user_input = input("\nYou: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ('quit', 'exit', 'q'):
                    break

                result = client.send_message_full(user_input)
                if result.get("thinking"):
                    print(f"[thinking] {result['thinking']}")
                print(result.get("response", ""))

            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"\n[Error] {e}")

    finally:
        client.close()


if __name__ == "__main__":
    main()
