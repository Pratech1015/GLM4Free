import json
import time
from playwright.sync_api import sync_playwright, Route

class ZaiClient:
    def __init__(self):
        self.responses = []
        self.browser = None
        self.page = None
        self.captured_body = None
        
    def start(self):
        """Start browser and navigate to Z.ai"""
        playwright = sync_playwright().start()
        self.browser = playwright.firefox.launch(
            headless=True,
            firefox_user_prefs={
                "general.useragent.override": "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
            }
        )
        
        context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Kolkata"
        )
        
        self.page = context.new_page()
        
        # Intercept API requests BEFORE navigating
        self.page.route("**/chat/completions*", self._handle_route)
        
        print("Loading Z.ai...")
        self.page.goto("https://chat.z.ai")
        self.page.wait_for_selector("#app", state="attached")
        self.page.wait_for_timeout(3000)
        
    def _handle_route(self, route: Route):
        """Intercept and capture the API request/response"""
        request = route.request
        url = request.url
        
        if "chat/completions" in url:
            print(f"\n[Intercepted] {url[:80]}...")
            
            # Continue and capture response
            route.fetch().then(lambda response: self._capture_response(route, response))
        else:
            route.continue_()
            
    def _capture_response(self, route: Route, response):
        """Capture the response body"""
        try:
            body = response.body()
            self.captured_body = body.decode('utf-8')
            print(f"[Captured] Response length: {len(self.captured_body)}")
        except Exception as e:
            print(f"[Error] Failed to capture: {e}")
        route.continue_()
    
    def wait_for_auth(self):
        """Wait for user to complete login/captcha"""
        print("\n" + "="*60)
        print("Waiting for authentication...")
        print("1. Complete any captcha if shown")
        print("2. Login if required (or use as guest)")
        print("3. Press ENTER when you're on the chat page ready")
        print("="*60)
        input("Press ENTER to continue...")
        
        token = self.page.evaluate("localStorage.getItem('token')")
        if token:
            print(f"[Auth] Token found: {token[:50]}...")
        else:
            print("[Auth] No token found (guest mode)")
            
    def send_message(self, message: str, timeout: int = 60):
        """Send a message using the UI"""
        
        print(f"\n[Action] Sending: {message}")
        self.captured_body = None
        
        # Find the textarea - try multiple selectors
        selectors = [
            'textarea',
            '[contenteditable="true"]',
            'div[contenteditable="true"]',
            'input[type="text"]'
        ]
        
        input_el = None
        for selector in selectors:
            try:
                # Check if element exists and is visible
                el = self.page.query_selector(selector)
                if el and el.is_visible():
                    # Check if it looks like a chat input
                    placeholder = el.get_attribute('placeholder') or ''
                    if any(kw in placeholder.lower() for kw in ['message', 'ask', 'send', '']) or selector == 'textarea':
                        input_el = el
                        print(f"[Found] Input: {selector}")
                        break
            except:
                continue
                
        if not input_el:
            # Take screenshot to debug
            self.page.screenshot(path="debug_input.png")
            print("[Error] Could not find input. Saved debug_input.png")
            return None
            
        # Click, type, and submit
        input_el.click()
        input_el.fill(message)
        
        # Try to submit
        try:
            # Look for send button
            send_btn = self.page.query_selector('button[type="submit"], button svg, button:has-text("Send")')
            if send_btn and send_btn.is_visible():
                send_btn.click()
                print("[Action] Clicked send button")
            else:
                input_el.press("Enter")
                print("[Action] Pressed Enter")
        except Exception as e:
            input_el.press("Enter")
            print(f"[Action] Pressed Enter (fallback): {e}")
            
        # Wait for response to complete
        print("[Waiting] For response...")
        
        # Wait for the AI response to appear in DOM
        try:
            # Wait for response message to appear (look for assistant role or new message)
            self.page.wait_for_selector(
                '[data-message-id]:last-child, .message:last-child, [role="assistant"], .assistant-message',
                timeout=timeout * 1000
            )
        except:
            pass
            
        # Alternative: wait and extract from DOM directly
        time.sleep(2)  # Give time for streaming
        
        # Try to extract response from DOM
        response_text = self._extract_from_dom()
        
        if response_text:
            print(f"\n{'='*60}")
            print("Response from DOM:")
            print(f"{'='*60}")
            print(response_text)
            return response_text
        else:
            print("[Error] Could not extract response")
            return None
            
    def _extract_from_dom(self):
        """Extract the last assistant message from DOM"""
        try:
            # Try different selectors for the response
            selectors = [
                # Look for last message that's not from user
                '.message:last-child .content',
                '.message.assistant:last-child',
                '[data-role="assistant"]:last-child',
                '.chat-message:last-child .message-content',
                # React-based selectors
                'div[class*="message"]:last-child',
                'div[class*="assistant"]:last-child'
            ]
            
            for selector in selectors:
                try:
                    elements = self.page.query_selector_all(selector)
                    if elements:
                        # Get the last one
                        last = elements[-1]
                        text = last.inner_text()
                        if text and len(text) > 10:  # Not empty
                            return text
                except:
                    continue
                    
            # Fallback: get all text from chat container
            chat_container = self.page.query_selector('.chat-container, .messages-container, [class*="chat"], [class*="message-list"]')
            if chat_container:
                return chat_container.inner_text()
                
        except Exception as e:
            print(f"[Extract Error] {e}")
            
        return None
            
    def close(self):
        if self.browser:
            self.browser.close()

def main():
    client = ZaiClient()
    
    try:
        client.start()
        client.wait_for_auth()
        
        while True:
            msg = input("\nEnter message (or 'quit' to exit): ")
            if msg.lower() == 'quit':
                break
            client.send_message(msg)
            
    finally:
        client.close()

if __name__ == "__main__":
    main()