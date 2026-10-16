# chat-engine-client

SDK بايثون لـ chat_engine. تبعية واحدة: `httpx`.

```bash
pip install -e sdk/python        # or: pip install chat-engine-client (CE-16 prepares publishing)
```

```python
from chat_engine_client import ChatEngine

with ChatEngine("http://localhost:8080") as ce:
    r = ce.chat("مرحبا")                               # يُنشئ محادثة
    r = ce.chat("تابع", conversation_id=r.conversation_id)
    for ev in ce.chat_stream("اشرح أكثر", conversation_id=r.conversation_id):
        print(ev.delta, end="")
    c = ce.completions("local-echo-1", [{"role": "user", "content": "hi"}])  # OpenAI-compatible
```

- Retry/backoff على 429/502/503/504 يحترم `Retry-After` و`details.retry_after_ms`.
- أخطاء: `ChatEngineAPIError{status, code, message, details, retryable}`.
- Async: `AsyncChatEngine` بنفس الواجهة (`async for ev in ce.chat_stream(...)`).
