"""A scripted OpenAI-compatible server over httpx.MockTransport (zero network)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


@dataclass
class FakeOpenAI:
    valid_keys: set[str] = field(default_factory=lambda: {"sk-test-valid-key-0001"})
    models: list[str] = field(default_factory=lambda: ["fake-chat-1", "fake-chat-2"])
    # scripted failures: status code to return on next N chat calls
    fail_next: list[int] = field(default_factory=list)
    retry_after: str | None = None
    calls: list[dict] = field(default_factory=list)
    seen_auth: list[str] = field(default_factory=list)
    slow: bool = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        self.seen_auth.append(auth)
        key = auth.removeprefix("Bearer ").strip()
        if key not in self.valid_keys:
            return httpx.Response(401, json={"error": {"message": "Incorrect API key provided", "type": "invalid_request_error", "code": "invalid_api_key"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": m, "object": "model", "context_window": 32768} for m in self.models]})
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            self.calls.append(body)
            if self.slow:
                raise httpx.ReadTimeout("slow", request=request)
            if self.fail_next:
                status = self.fail_next.pop(0)
                headers = {"retry-after": self.retry_after} if self.retry_after else {}
                return httpx.Response(status, headers=headers, json={"error": {"message": "scripted", "type": "server_error", "code": f"scripted_{status}"}})
            if body["model"] not in self.models:
                return httpx.Response(404, json={"error": {"message": "model not found", "code": "model_not_found"}})
            last_user = next((m["content"] for m in reversed(body["messages"]) if m["role"] == "user"), "")
            text = "ok" if "single word" in str(last_user) else f"echo:{last_user}"
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "model": body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
                },
            )
        return httpx.Response(404, json={"error": {"message": "no route"}})
