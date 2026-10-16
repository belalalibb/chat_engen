"""chat_engine_client — typed Python SDK for chat_engine (CE-11).

Zero dependencies beyond ``httpx``. Sync (:class:`ChatEngine`) and async
(:class:`AsyncChatEngine`) clients; SSE streaming; retry/backoff that honours
``Retry-After`` / ``details.retry_after_ms`` on 429/502/503/504.

    from chat_engine_client import ChatEngine
    ce = ChatEngine("http://localhost:8080")
    r = ce.chat("hello")                       # creates a conversation
    r2 = ce.chat("and again", conversation_id=r.conversation_id)
    for ev in ce.chat_stream("stream this", conversation_id=r.conversation_id):
        print(ev.delta, end="")
"""

from __future__ import annotations

from chat_engine_client.client import AsyncChatEngine, ChatEngine
from chat_engine_client.errors import ChatEngineAPIError, RetryExhausted
from chat_engine_client.models import ChatResult, Completion, StreamEvent, Turn

__version__ = "0.1.0"
__all__ = [
    "AsyncChatEngine",
    "ChatEngine",
    "ChatEngineAPIError",
    "ChatResult",
    "Completion",
    "RetryExhausted",
    "StreamEvent",
    "Turn",
    "__version__",
]
