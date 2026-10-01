# GLM4Free

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Async](https://img.shields.io/badge/async-supported-green.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-working-brightgreen.svg)

Async Z.ai/GLM chat stack: a pure-HTTP API client, an async Playwright browser client, and an OpenAI-compatible server with streaming.

> [!WARNING]
> Built on reverse-engineered Z.ai infrastructure. API changes may break functionality without notice.

> [!IMPORTANT]
> Credentials are required before the API client works. Run `await ZaiApiClient.bootstrap()` once — it fetches a fresh guest token and captcha purely over HTTP, no browser needed (falls back to the browser if the solver gets risk-blocked).

---

## Overview

* pure-HTTP Z.ai client — signed requests, SSE streaming, auto captcha and session refresh
* async Playwright browser client with in-page SSE interception
* OpenAI-compatible server (`/v1/chat/completions`, SSE) on port 3016
* conversation continuation via `conversation_id` or `X-Session-Id`
* web search, deep think (`low`/`high`/`max`), and attachments (login)
* guest-token auto refresh on 401

---

## Installation

```bash
pip install aiohttp playwright google-generativeai cryptography
playwright install firefox
```

Credentials are stored under `glmpp/` (gitignored).

---

## Quick Start

### Bootstrap Credentials

```python
import asyncio
from glmpp.api import ZaiApiClient

client = await ZaiApiClient.bootstrap()   # pure HTTP, saves credentials
```

### Start The Server

```bash
cd glmpp
python main.py
```

### Log In (Optional — Unlocks More Models)

```bash
python glmpp/login.py
```

Opens Playwright Firefox at `https://chat.z.ai/auth`. Log in normally — the script snatches the `Authorization` bearer token from browser traffic, grabs cookies, queries `/api/models`, and saves everything to `glmpp/.zai_credentials.json`.

> [!NOTE]
> The browser session is remembered in `glmpp/.zai_browser_state.json`. Reruns restore it and skip login while the token stays valid; if it expired, the script clears it and asks you to log in again. `client.py` reuses the same file, so it starts logged in too.

> [!NOTE]
> Logged-in credentials unlock **glm-5.2** and **glm-5.3** in `/v1/models` alongside `glm-5.3-flash`. If the token expires or gets downgraded to guest, rerun `python glmpp/login.py`.

> [!TIP]
> Pass `--timeout 900` if you need more time to complete the login.

> [!NOTE]
> The server loads saved credentials automatically and re-bootstraps the session on 401.

---

## Project Structure

```
glmpp/
│
├── api.py            # async pure-HTTP Z.ai client
├── captcha.py        # pure-HTTP Aliyun captcha flow
├── client.py         # async Playwright browser client
├── login.py          # capture logged-in bearer token (unlocks glm-5.2 / glm-5.3)
├── main.py           # OpenAI-compatible server
├── setup.py          # one-time browser credential extract
├── js/               # vendored captcha builders (Node)
└── README.md
```

---

## Usage

### Async API Client

```python
import asyncio
from glmpp.api import ZaiApiClient

async def main():
    client = ZaiApiClient.auto_init()     # load saved credentials

    # One-shot
    reply = await client.send_message("Hello!")
    print(reply)

    # Streaming
    async for chunk in client.send_message_stream("Tell me a story"):
        print(chunk, end="", flush=True)

    # With thinking
    result = await client.send_message_full("Explain relativity")
    print("Thinking:", result["thinking"])

    # Per-request options (same fields the server accepts)
    reply = await client.send_message(
        "Latest AI news?",
        web_search=True,              # or advanced_web_search=True
        deep_think=True,
        reasoning_effort="low",       # low | high | max
        attachments=["/path/to/img.png"],   # login required
    )

    await client.close()

asyncio.run(main())
```

> [!TIP]
> `send_message_stream` also accepts `chat_id=` to pin a specific Z.ai conversation, plus the same option kwargs (`web_search`, `advanced_web_search`, `deep_think`, `reasoning_effort`, `attachments`).

---

### Browser Client

```python
import asyncio
from glmpp.client import ZaiClient

async def chat():
    async with ZaiClient(headless=True) as client:
        await client.wait_for_auth()      # solve captcha in browser if prompted
        async for chunk in client.send_message_stream("Hello"):
            print(chunk, end="", flush=True)

asyncio.run(chat())
```

Interactive chat (streams live):

```bash
python glmpp/client.py
```

---

### OpenAI-Compatible Server

```bash
curl -N http://127.0.0.1:3016/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","stream":true,"messages":[{"role":"user","content":"Hi"}]}'
```

Models: `glm-5.3-flash` (Z.ai), plus Gemini personalities (`boxar-1`, `yui`, `kurumi-tokisaki`, …). After `login.py`, also `glm-5.2` and `glm-5.3`.

Request extras: web search, deep think / reasoning effort, and attachments — see the sections below.

---

### Continuing A Conversation

Every `glm-5.3-flash` response includes a `conversation_id` — pass it back to keep the same conversation:

```bash
# body field: conversation_id | session_id | chat_id
curl -N http://127.0.0.1:3016/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","stream":true,"conversation_id":"conv_123","messages":[...]}'

# or header: X-Session-Id | X-Conversation-Id | X-Chat-Id
curl -N http://127.0.0.1:3016/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'X-Session-Id: conv_123' \
  -d '{"model":"glm-5.3-flash","stream":true,"messages":[...]}'
```

> [!NOTE]
> Mappings persist to `glmpp/.zai_conversations.json` and survive restarts. List with `GET /v1/conversations`, clear with `DELETE /v1/conversations`. Always send the full `messages` history — context comes from your messages; the id only pins the same Z.ai chat.

---

### Web Search

Toggle Z.ai's built-in web search per request:

```bash
curl -N http://127.0.0.1:3016/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","stream":true,"web_search":true,"messages":[{"role":"user","content":"Latest AI news?"}]}'
```

| Field | Effect |
|---|---|
| `web_search` | Standard web search (`auto_web_search`) |
| `advanced_web_search` | Advanced search (search + `web_search` features) |

---

### Deep Think

```bash
curl -N http://127.0.0.1:3016/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","stream":true,"deep_think":true,"reasoning_effort":"low","messages":[{"role":"user","content":"Solve: 2x+5=17"}]}'
```

| Field | Values | Default |
|---|---|---|
| `deep_think` | `true` / `false` | `true` |
| `reasoning_effort` | `low` / `high` / `max` | `max` |

> [!NOTE]
> `reasoning_effort` is only sent while `deep_think` is enabled, matching the Z.ai frontend.

---

### Attachments

> [!IMPORTANT]
> Attachments upload through `POST /api/v1/files/`, which rejects guest tokens — **login required** (`python glmpp/login.py`).

```bash
curl -N http://127.0.0.1:3016/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","stream":true,"attachments":["/path/to/image.png"],"messages":[{"role":"user","content":"Describe this"}]}'
```

`attachments` accepts local paths, `https://` URLs, and `data:` URIs. OpenAI-style content parts work too:

```json
{"role":"user","content":[
  {"type":"text","text":"Describe this"},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,...."}}
]}
```

> [!NOTE]
> Images become `image_url` parts, videos `video_url`, everything else `file_url` (text extraction enabled). Already-uploaded Z.ai file ids pass through untouched.

---

## Architecture

### 1. API Layer (`api.py`)

Pure-HTTP async client: request signing, SSE parsing, conversation creation, and automatic recovery — captcha refresh on failure, guest-token re-auth on 401.

### 2. Captcha Layer (`captcha.py`)

Browser-free Aliyun TRACELESS flow built on a known-good device identity, with the `pe.059` data builder vendored under `js/` and run in Node.

### 3. Server Layer (`main.py`)

aiohttp server exposing OpenAI-compatible endpoints, mapping `conversation_id` to Z.ai chat ids and flattening client history into each upstream request.

---

## Notes

> [!CAUTION]
> This project is experimental and based on reverse-engineered behavior of Z.ai's infrastructure. The API may change at any time, and use may violate Z.ai's terms of service. Use at your own risk.

> [!TIP]
> If requests start failing with 401, just call `await client.refresh_session()` — it fetches a fresh guest token and captcha without a browser.

> [!TIP]
> Captcha generation is occasionally flaky; retry once or twice before falling back to the browser. If the pure-HTTP solver gets risk-blocked (Aliyun `F001`), the client automatically falls back to a real browser (`playwright install firefox`) for the refresh.

---

## License

This project is licensed under the [MIT License](LICENSE).
