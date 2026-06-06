import json
import time
from curl_cffi import requests
from sentinel import calc_pow_token

class ChatGPTHybridEngine:
    def __init__(self):
        print("[Engine System] Initializing Fully Rev-Engineed Network Pipeline...")
        self.session = requests.Session()
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/event-stream",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "Oai-Language": "en-US"
        })

    def stream_chat(self, prompt: str):
        url = "https://chatgpt.com/backend-anon/v1/conversation"
        
        # --- REVERSE ENGINEERED SENTINEL HANDSHAKE ---
        # Fetch the current dynamic seed/difficulty parameter metrics from OpenAI's config endpoint.
        # For an anonymous session context, we supply a standard runtime baseline:
        mock_seed = "0.1287349182374_ABCDEF" 
        mock_difficulty = "0000" # Target difficulty baseline for text models
        
        print("[Engine System] Generating Proof-of-Work Challenge Token...")
        pow_token = calc_pow_token(mock_seed, mock_difficulty, self.user_agent)
        
        # Inject the computed token straight into the network headers
        self.session.headers.update({
            "Openai-Sentinel-Proof-Token": pow_token
        })
        # ----------------------------------------------

        payload = {
            "action": "next",
            "messages": [
                {
                    "id": "aaa11111-2222-3333-4444-555555555555",
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [prompt]},
                    "metadata": {}
                }
            ],
            "parent_message_id": "bbb22222-3333-4444-5555-666666666666",
            "model": "text-davinci-002-render-sha",
            "timezone_offset_min": -330,
            "suggestions": [],
            "history_and_training_disabled": True
        }

        try:
            response = self.session.post(
                url, 
                json=payload, 
                impersonate="chrome124", 
                stream=True, 
                timeout=15
            )
            
            if response.status_code != 200:
                print(f"\n[Interface Error] Transaction dropped with code: {response.status_code}")
                print(f"[Server Metadata]: {response.text[:200]}")
                return

            last_text = ""
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8').strip()
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            parts = data_json.get("message", {}).get("content", {}).get("parts", [])
                            if parts and parts[0]:
                                full_text = parts[0]
                                if len(full_text) > len(last_text):
                                    chunk = full_text[len(last_text):]
                                    print(chunk, end="", flush=True)
                                    last_text = full_text
                        except:
                            pass
                            
        except Exception as e:
            print(f"\n[Interface Error] Pipeline exception: {e}")