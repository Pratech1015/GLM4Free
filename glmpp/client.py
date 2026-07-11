#!/usr/bin/env python3
"""
Z.ai Browser Automation Client
Uses Playwright to intercept API responses and interact with Z.ai
"""

import time
import random
from typing import Optional, List
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext


@dataclass
class ChatMessage:
    role: str  # 'user' or 'assistant'
    content: str


class ZaiClient:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.messages: List[ChatMessage] = []
        self._playwright = None
        
    def start(self) -> None:
        """Initialize browser and navigate to Z.ai"""
        self.close()
        self._playwright = sync_playwright().start()
        
        self.browser = self._playwright.firefox.launch(
            headless=self.headless,
            firefox_user_prefs={
                "general.useragent.override": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                    "Gecko/20100101 Firefox/128.0"
                ),
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False,
                "privacy.trackingprotection.enabled": True,
                "media.navigator.enabled": False,
                "webgl.disabled": False,
                "pdfjs.disabled": False,
                "network.cookie.lifetimePolicy": 0,
                "privacy.resistFingerprinting": False,
                "font.system.whitelist": "",
                "browser.cache.disk.enable": True,
                "browser.cache.memory.enable": True,
                "browser.cache.offline.enable": True,
                "browser.sessionstore.resume_from_crash": True,
                "geo.enabled": False,
                "media.peerconnection.enabled": True,
                "webgl.enable-webgl2": True,
            }
        )
        
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            ),
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        
        self.context.add_init_script("""
            // Override navigator properties to remove automation flags
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Mock plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                ]
            });
            
            // Mock languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Mock platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Linux x86_64'
            });
            
            // Mock hardware concurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
            
            // Mock device memory
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8
            });
            
            // Mock maxTouchPoints
            Object.defineProperty(navigator, 'maxTouchPoints', {
                get: () => 0
            });
            
            // Fix chrome object
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {},
            };
            
            // Fix permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) =>
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters);
            
            // Remove automation-related properties
            delete navigator.__proto__.webdriver;
            
            // === SSE Stream Interceptor ===
            // Monkey-patch fetch to intercept /api/v2/chat/completions SSE streams
            const _origFetch = window.fetch;
            window.fetch = async function(...args) {
                const resp = await _origFetch.apply(this, args);
                const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
                
                if (url.includes('/api/v2/chat/completions')) {
                    const contentType = resp.headers.get('content-type') || '';
                    if (contentType.includes('text/event-stream')) {
                        const reader = resp.body.getReader();
                        const decoder = new TextDecoder();
                        let buffer = '';
                        let thinking = '';
                        let answer = '';
                        
                        window.__zai_sse = { thinking: '', answer: '', done: false, buffer: '' };
                        
                        const stream = new ReadableStream({
                            start(controller) {
                                function pump() {
                                    reader.read().then(({ done, value }) => {
                                        if (done) {
                                            window.__zai_sse.done = true;
                                            controller.close();
                                            return;
                                        }
                                        buffer += decoder.decode(value, { stream: true });
                                        window.__zai_sse.buffer = buffer;
                                        
                                        // Parse SSE lines
                                        const lines = buffer.split('\\n');
                                        buffer = lines.pop(); // keep incomplete line
                                        
                                        for (const line of lines) {
                                            if (!line.startsWith('data: ')) continue;
                                            const jsonStr = line.slice(6).trim();
                                            if (!jsonStr || jsonStr === '[DONE]') continue;
                                            try {
                                                const obj = JSON.parse(jsonStr);
                                                if (obj.type === 'chat:completion' && obj.data) {
                                                    const delta = obj.data.delta_content || '';
                                                    const phase = obj.data.phase || '';
                                                    if (phase === 'thinking') {
                                                        thinking += delta;
                                                        window.__zai_sse.thinking = thinking;
                                                    } else {
                                                        answer += delta;
                                                        window.__zai_sse.answer = answer;
                                                    }
                                                }
                                            } catch(e) {}
                                        }
                                        
                                        controller.enqueue(value);
                                        pump();
                                    }).catch(err => {
                                        window.__zai_sse.done = true;
                                        controller.error(err);
                                    });
                                }
                                pump();
                            }
                        });
                        
                        return new Response(stream, {
                            status: resp.status,
                            statusText: resp.statusText,
                            headers: resp.headers
                        });
                    }
                }
                return resp;
            };
        """)
        
        self.page = self.context.new_page()
        
        print("=" * 60)
        print("Loading Z.ai...")
        print("=" * 60)
        
        self.page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=60000)
        
        # Wait for React app to mount
        self.page.wait_for_selector("#app", state="attached", timeout=60000)
        self.page.wait_for_timeout(5000)
        
        print("[Ready] Page loaded")
        
    def wait_for_auth(self) -> Optional[str]:
        """Wait for user to complete authentication"""
        print("\n" + "=" * 60)
        print("AUTHENTICATION REQUIRED")
        print("=" * 60)
        print("1. Complete any captcha if shown")
        print("2. Login if required (or continue as guest)")
        print("3. Wait until you see the chat interface")
        print("4. Press ENTER to continue")
        print("=" * 60)
        
        input("Press ENTER when ready...")
        
        # Check for token
        token = self.page.evaluate("localStorage.getItem('token')")
        if token:
            print(f"[Auth] Logged in with token: {token[:50]}...")
            return token
        else:
            print("[Auth] Using guest mode (no token)")
            return None
            
    def _find_element(self, selectors: List[str], timeout: int = 5000):
        """Try multiple selectors to find an element"""
        for selector in selectors:
            try:
                el = self.page.wait_for_selector(selector, timeout=timeout // len(selectors))
                if el and el.is_visible():
                    return el
            except:
                continue
        return None

    def send_message(self, message: str, wait_for_response: bool = True) -> Optional[str]:
        """
        Send a message and extract response from SSE stream
        
        Args:
            message: The message text to send
            wait_for_response: Whether to wait for and extract the response
            
        Returns:
            The AI's response text, or None if extraction failed
        """
        if not self.page:
            raise RuntimeError("Client not started. Call start() first.")
            
        print(f"\n[User] {message}")
        
        # Reset SSE state
        self.page.evaluate("window.__zai_sse = { thinking: '', answer: '', done: false, buffer: '' }")
        
        # Step 1: Find and focus the textarea
        textarea = self._find_element([
            '#chat-input',
            'textarea[id="chat-input"]',
            'textarea',
            'textarea[placeholder]',
            'div textarea',
            '[contenteditable="true"]'
        ])
        
        if not textarea:
            self._save_debug_screenshot("no_textarea")
            raise RuntimeError("Could not find message input textarea")
            
        # Step 2: Click to focus, then type with human-like delays
        textarea.click()
        self.page.wait_for_timeout(200 + (hash(message) % 300))
        
        # Type character by character with random delays
        for char in message:
            textarea.type(char, delay=50 + (hash(char) % 80))
        
        # Small delay for React state update
        self.page.wait_for_timeout(300 + (hash(message) % 200))
        
        # Step 3: Find and click the send button
        send_clicked = False
        
        # Try the button by ID first
        send_btn = self.page.query_selector('#send-message-button')
        if send_btn:
            self.page.evaluate("""
                () => {
                    const btn = document.querySelector('#send-message-button');
                    if (btn) btn.disabled = false;
                }
            """)
            self._human_delay(150, 400)
            self._random_mouse_move()
            
            try:
                send_btn.click()
                send_clicked = True
                print("[Action] Clicked send button")
            except Exception as e:
                print(f"[Warning] Button click failed: {e}")
                
        if not send_clicked:
            container = self.page.query_selector('[aria-label="Send Message"]')
            if container:
                try:
                    container.click()
                    send_clicked = True
                    print("[Action] Clicked send container")
                except Exception as e:
                    print(f"[Warning] Container click failed: {e}")
                    
        if not send_clicked:
            textarea.press("Enter")
            print("[Action] Pressed Enter to send")
            
        if not wait_for_response:
            return None
            
        # Step 4: Wait for SSE stream to complete and extract response
        print("[Waiting] For AI response...")
        return self._wait_for_sse_response()
        
    def _wait_for_sse_response(self, timeout: int = 120) -> Optional[str]:
        """Wait for SSE stream to finish, streaming output to console"""
        start_time = time.time()
        last_len = 0
        in_thinking = False
        thinking_printed = False
        thinking_len = 0
        answer_len = 0
        
        while time.time() - start_time < timeout:
            try:
                sse = self.page.evaluate("window.__zai_sse || {}")
                done = sse.get('done', False)
                thinking = sse.get('thinking', '')
                answer = sse.get('answer', '')
                
                # Stream thinking phase
                if thinking and not thinking_printed:
                    if not in_thinking:
                        in_thinking = True
                        print("\n[Thinking]", flush=True)
                    if len(thinking) > thinking_len:
                        new_text = thinking[thinking_len:]
                        print(new_text, end="", flush=True)
                        thinking_len = len(thinking)
                
                # Stream answer phase
                if answer:
                    if in_thinking and not thinking_printed:
                        print("\n\n[Response]", end="", flush=True)
                        thinking_printed = True
                        in_thinking = False
                    if len(answer) > answer_len:
                        new_text = answer[answer_len:]
                        print(new_text, end="", flush=True)
                        answer_len = len(answer)
                
                # Done
                if done:
                    if in_thinking and not thinking_printed:
                        # Was thinking only, no answer
                        print()
                        thinking_printed = True
                    if not answer and not thinking:
                        # SSE not intercepted, try DOM
                        dom_text = self._extract_last_assistant_message_from_dom()
                        if dom_text:
                            print(dom_text)
                            return dom_text
                    elif answer:
                        print()  # final newline
                        result = ""
                        if thinking:
                            result += f"[Thinking]\n{thinking}\n\n[Response]\n{answer}"
                        else:
                            result = answer
                        return result
                    elif thinking:
                        print()
                        return f"[Thinking]\n{thinking}"
                    break
                    
            except Exception as e:
                print(f"\n[Debug] SSE poll error: {e}")
            time.sleep(0.3)
            
        print("\n[Error] Response timeout")
        return None
        
    def _extract_last_assistant_message_from_dom(self) -> str:
        """Fallback: extract from DOM with style tag stripping"""
        try:
            return self.page.evaluate("""
                () => {
                    function cleanNode(el) {
                        const clone = el.cloneNode(true);
                        clone.querySelectorAll('style, script, noscript, svg').forEach(s => s.remove());
                        clone.querySelectorAll('.thinking-chain-container, .thinking-block').forEach(tb => tb.remove());
                        return (clone.innerText || '').trim();
                    }
                    const blocks = document.querySelectorAll('.chat-assistant .markdown-prose');
                    if (blocks.length > 0) {
                        return cleanNode(blocks[blocks.length - 1]);
                    }
                    return '';
                }
            """)
        except Exception:
            return ""
        
    def get_chat_history(self) -> List[ChatMessage]:
        """Get full chat history - attempts DOM extraction"""
        try:
            result = self.page.evaluate("""
                () => {
                    const messages = [];
                    function cleanNode(el) {
                        const clone = el.cloneNode(true);
                        clone.querySelectorAll('style, script, noscript, svg').forEach(s => s.remove());
                        clone.querySelectorAll('.thinking-chain-container, .thinking-block').forEach(tb => tb.remove());
                        return (clone.innerText || '').trim();
                    }
                    
                    document.querySelectorAll('.chat-user .markdown-prose').forEach(el => {
                        const text = cleanNode(el);
                        if (text.length > 0) messages.push({role: 'user', content: text});
                    });
                    document.querySelectorAll('.chat-assistant .markdown-prose').forEach(el => {
                        const text = cleanNode(el);
                        if (text.length > 0) messages.push({role: 'assistant', content: text});
                    });
                    return messages;
                }
            """)
            return [ChatMessage(m['role'], m['content']) for m in result]
        except Exception:
            return []
        
    def _random_mouse_move(self):
        """Simulate random mouse movement"""
        x = random.randint(100, 1800)
        y = random.randint(100, 900)
        self.page.mouse.move(x, y)
        self.page.wait_for_timeout(50 + random.randint(0, 100))
        
    def _human_delay(self, min_ms: int = 100, max_ms: int = 500):
        """Add random human-like delay"""
        self.page.wait_for_timeout(random.randint(min_ms, max_ms))
        
    def _save_debug_screenshot(self, name: str):
        """Save screenshot for debugging"""
        if self.page:
            filename = f"debug_{name}_{int(time.time())}.png"
            self.page.screenshot(path=filename)
            print(f"[Debug] Screenshot saved: {filename}")
            
    def close(self):
        """Clean up and close browser"""
        if self.browser:
            self.browser.close()
            self.browser = None
            self.context = None
            self.page = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        print("\n[Closed] Browser session ended")
            
    def __enter__(self):
        self.start()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
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
                    
                response = client.send_message(user_input)
                
                if not response:
                    print("\n[Error] No response received")
                    
            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"\n[Error] {e}")
                
    finally:
        client.close()


if __name__ == "__main__":
    main()
