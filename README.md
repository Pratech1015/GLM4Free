# GLM4Free

GLM4Free API (with streaming and more features)

## Z.ai Browser Client

A Playwright-based client for interacting with [chat.z.ai](https://chat.z.ai) via browser automation. Intercepts SSE streams to capture raw responses with thinking process.

### Install

```bash
pip install playwright
playwright install firefox
```

### Quick Start

```python
from glmpp.client import ZaiClient

client = ZaiClient()
client.start()
client.wait_for_auth()

response = client.send_message("Hello!")
print(response)

client.close()
```

### Usage

#### One-shot

```python
from glmpp.client import ZaiClient

with ZaiClient() as client:
    client.wait_for_auth()
    response = client.send_message("What is quantum computing?")
    print(response)
```

#### Streaming

```python
with ZaiClient() as client:
    client.wait_for_auth()
    for chunk in client.send_message_stream("Tell me a story"):
        print(chunk, end="", flush=True)
    print()
```

#### With Thinking Process

```python
with ZaiClient() as client:
    client.wait_for_auth()
    result = client.send_message_full("Explain relativity")
    print("Thinking:", result["thinking"])
    print("Response:", result["response"])
```

#### Chat History

```python
history = client.get_chat_history()
for msg in history:
    print(f"{msg.role}: {msg.content}")
```

### API Reference

| Method | Returns | Description |
|--------|---------|-------------|
| `send_message(text)` | `str` | Send message, get full response |
| `send_message_stream(text)` | `Generator[str]` | Send message, yield response chunks |
| `send_message_full(text)` | `dict` | Get `{"thinking": str, "response": str}` |
| `get_chat_history()` | `List[ChatMessage]` | Get all messages from session |
| `wait_for_auth()` | `str \| None` | Wait for captcha/login (interactive) |
| `start()` | `None` | Launch browser, load Z.ai |
| `close()` | `None` | Close browser and cleanup |

### Interactive Mode

```bash
python -m glmpp.client
```

Opens a chat session in the terminal. Type your messages and get streaming responses.

### Requirements

- Python 3.10+
- `playwright` with Firefox installed
- Internet connection
