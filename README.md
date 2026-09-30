# GLM4Free

GLM4Free API — async OpenAI-compatible server plus Z.ai browser/API clients (with streaming).

## Install

```bash
pip install aiohttp playwright google-generativeai cryptography
playwright install firefox
```

Credentials live under `glmpp/` (see `.gitignore`). Run one-time setup if needed:

```bash
cd glmpp
python setup.py            # headless browser credential extract
python setup.py --visible  # show browser window
```

After setup, the pure-HTTP API client works without a browser.

---

## 1. Async API client (`glmpp/api.py`)

Pure HTTP — no browser. Signs requests, streams SSE, auto-refreshes captcha.

### Quick start

```python
import asyncio
from glmpp.api import ZaiApiClient

async def main():
    client = ZaiApiClient.auto_init()
    if not client:
        raise SystemExit("No credentials. Run: python glmpp/setup.py")

    # One-shot
    reply = await client.send_message("Hello!")
    print(reply)

    # Streaming (answer deltas only)
    async for chunk in client.send_message_stream("Tell me a story"):
        print(chunk, end="", flush=True)
    print()

    # Full response with thinking
    result = await client.send_message_full("Explain relativity")
    print("Thinking:", result["thinking"])
    print("Response:", result["response"])

asyncio.run(main())
```

### API reference

| Method | Returns | Description |
|--------|---------|-------------|
| `ZaiApiClient.auto_init()` | `ZaiApiClient \| None` | Load creds from env / `.zai_credentials.json` |
| `await send_message(text)` | `str` | Append to history, return full reply |
| `send_message_stream(text)` | `AsyncGenerator[str]` | Yield answer chunks |
| `await send_message_full(text)` | `dict` | `{"thinking": str, "response": str}` |
| `await refresh_captcha()` | `str` | Fresh single-use captcha (pure HTTP; browser fallback) |
| `await create_conversation()` | `str` | New server-side chat id |
| `await close()` | `None` | Close aiohttp session |

---

## 2. Browser client (`glmpp/client.py`)

Playwright + Firefox. Intercepts chat.z.ai SSE in-page and streams tokens.

### Interactive chat (streams live)

```bash
python glmpp/client.py
```

### In code

```python
from glmpp.client import ZaiClient

client = ZaiClient(headless=True)
client.start()
client.wait_for_auth()          # complete captcha in browser if prompted

# Streaming answer only
for chunk in client.send_message_stream("Hello"):
    print(chunk, end="", flush=True)
print()

# Streaming thinking + answer as (phase, delta)
for phase, delta in client.send_message_stream_full("Explain relativity"):
    print(f"[{phase}] {delta}", end="", flush=True)
print()

# Non-streaming
reply = client.send_message("Hello")
full = client.send_message_full("Hello")   # {"thinking", "response"}

client.close()
```

Context manager:

```python
with ZaiClient(headless=True) as client:
    client.wait_for_auth()
    async for _ in []:  # or use send_message / send_message_stream
        pass
    print(client.send_message("Hi"))
```

### API reference

| Method | Returns | Description |
|--------|---------|-------------|
| `start()` | `None` | Launch Firefox, load chat.z.ai, inject SSE interceptor |
| `wait_for_auth()` | `str \| None` | Pause for captcha/login, return token |
| `send_message(text)` | `str` | Send, wait, return answer |
| `send_message_stream(text)` | `Generator[str]` | Yield answer chunks (optionally `include_thinking=True` → `"thinking:"`/`"answer:"` prefixes) |
| `send_message_stream_full(text)` | `Generator[tuple[str, str]]` | Yield `(phase, delta)` where phase is `thinking` or `answer` |
| `send_message_full(text)` | `dict` | `{"thinking": str, "response": str}` |
| `get_chat_history()` | `list[ChatMessage]` | Read messages from DOM |
| `close()` | `None` | Close browser |

---

## 3. OpenAI-compatible server (`glmpp/main.py`)

Serves `/v1/chat/completions` (SSE when `"stream": true`) on port **3016**.

```bash
python glmpp/main.py
```

Models: `glm-4` (Z.ai via pure-HTTP client), plus Gemini personalities (`boxar-1`, `yui`, `kurumi-tokisaki`, …).

Example:

```bash
curl -N http://127.0.0.1:3016/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-4","stream":true,"messages":[{"role":"user","content":"Hi"}]}'
```

---

## Requirements

- Python 3.10+
- `aiohttp`, `cryptography`
- `playwright` + Firefox (browser client / one-time setup only)
- Internet connection
