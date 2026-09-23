# GLM4Free

GLM4Free API (with streaming and more features)

## Z.ai Client

A lightweight async browser client for [chat.z.ai](https://chat.z.ai). Uses Playwright async API with SSE response interception.

### Install

```bash
pip install playwright
playwright install firefox
```

### Quick Start

```python
from hybrid import ZaiClient
import asyncio

async def main():
    async with ZaiClient() as client:
        await client.wait_for_auth()
        response = await client.send_message("Hello!")
        print(response)

asyncio.run(main())
```

### Usage

#### One-shot

```python
async with ZaiClient() as client:
    await client.wait_for_auth()
    response = await client.send_message("What is quantum computing?")
    print(response)
```

#### Streaming

```python
async with ZaiClient() as client:
    await client.wait_for_auth()
    async for chunk in client.send_message_stream("Tell me a story"):
        print(chunk, end="", flush=True)
    print()
```

#### With Thinking Process

```python
async with ZaiClient() as client:
    await client.wait_for_auth()
    result = await client.send_message_full("Explain relativity")
    print("Thinking:", result["thinking"])
    print("Response:", result["response"])
```

### API Reference

| Method | Returns | Description |
|--------|---------|-------------|
| `send_message(text)` | `str` | Send message, get full response |
| `send_message_stream(text)` | `AsyncGenerator[str]` | Send message, yield response chunks |
| `send_message_full(text)` | `dict` | Get `{"thinking": str, "response": str}` |
| `start()` | `None` | Launch browser, load Z.ai |
| `close()` | `None` | Close browser and cleanup |
| `wait_for_auth()` | `None` | Wait for captcha/login (interactive) |

### Requirements

- Python 3.10+
- `playwright` with Firefox installed
- Internet connection
