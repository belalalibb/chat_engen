"""Sync + async clients. Every method maps 1:1 to a documented route (docs/API.md)."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

import httpx

from chat_engine_client.errors import ChatEngineAPIError, RetryExhausted
from chat_engine_client.models import ChatResult, Completion, StreamEvent

RETRYABLE = {429, 502, 503, 504}
JSON = dict[str, Any]


def _backoff(attempt: int, err: ChatEngineAPIError | None, headers: httpx.Headers | None) -> float:
    if err is not None and err.retry_after_s is not None:
        return err.retry_after_s
    if headers is not None and headers.get("retry-after"):
        try:
            return float(headers["retry-after"])
        except ValueError:
            pass
    return min(8.0, 0.25 * (2**attempt)) * (0.5 + random.random())


def parse_sse(lines: Iterable[str]) -> Iterator[StreamEvent]:
    """Tolerant SSE parser: id/event/data lines, blank-line delimited, ``[DONE]`` ends."""
    event_id: str | None = None
    data_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                if payload == "[DONE]":
                    return
                d = json.loads(payload)
                yield StreamEvent(
                    seq=int(d.get("seq", 0)),
                    type=str(d.get("type", "")),
                    stream_id=str(d.get("stream_id", "")),
                    data=dict(d.get("data") or {}),
                    event_id=event_id or d.get("event_id"),
                )
            event_id = None
            continue
        if line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if key == "id":
            event_id = value
        elif key == "data":
            data_lines.append(value)


def _chat_body(
    message: str | None,
    conversation_id: str | None,
    project_id: str | None,
    model: str | None,
    messages: list[JSON] | None,
    extra: JSON,
) -> JSON:
    body: JSON = dict(extra)
    if message is not None:
        body["message"] = message
    if conversation_id:
        body["conversation_id"] = conversation_id
    if project_id:
        body["project_id"] = project_id
    if model:
        body["model"] = model
    if messages is not None:
        body["messages"] = messages
    return body


class _Base:
    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        h = {"accept": "application/json", **(headers or {})}
        if api_key:
            h["authorization"] = f"Bearer {api_key}"
        self._headers = h

    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        try:
            body = resp.json()
        except ValueError:
            body = {"error": {"message": resp.text, "code": f"http_{resp.status_code}"}}
        raise ChatEngineAPIError(resp.status_code, body)

    def _final(self, resp: httpx.Response, attempt: int) -> None:
        """Last attempt: a still-retryable status after ≥1 retry ⇒ RetryExhausted."""
        if attempt > 0 and resp.status_code in RETRYABLE:
            try:
                self._raise(resp)
            except ChatEngineAPIError as e:
                raise RetryExhausted(e.status, e.body) from None
        self._raise(resp)


class ChatEngine(_Base):
    """Synchronous client."""

    def __init__(self, base_url: str = "http://localhost:8080", **kw: Any) -> None:
        transport = kw.pop("transport", None)
        super().__init__(base_url, **kw)
        self._http = httpx.Client(
            base_url=self.base_url, headers=self._headers, timeout=self.timeout, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ChatEngine:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kw: Any) -> JSON:
        last: ChatEngineAPIError | None = None
        for attempt in range(self.max_retries + 1):
            resp = self._http.request(method, path, **kw)
            if resp.status_code in RETRYABLE and attempt < self.max_retries:
                try:
                    self._raise(resp)
                except ChatEngineAPIError as e:
                    last = e
                time.sleep(_backoff(attempt, last, resp.headers))
                continue
            self._final(resp, attempt)
            return {} if resp.status_code == 204 else dict(resp.json())
        raise RetryExhausted(last.status if last else 503, last.body if last else {})

    def _stream(self, method: str, path: str, body: JSON) -> Iterator[StreamEvent]:
        with self._http.stream(
            method, path, json=body, headers={"accept": "text/event-stream"}
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                self._raise(resp)
            yield from parse_sse(resp.iter_lines())

    # -- health / models -----------------------------------------------------------------------

    def health(self) -> JSON:
        return self._request("GET", "/healthz")

    def models(self) -> list[JSON]:
        return list(self._request("GET", "/v1/openai/models")["data"])

    def catalog(self, *, offset: int = 0, limit: int = 50, q: str | None = None) -> JSON:
        params: JSON = {"offset": offset, "limit": limit}
        if q:
            params["q"] = q
        return self._request("GET", "/v1/catalog/models", params=params)

    # -- projects / conversations ----------------------------------------------------------------

    def create_project(self, name: str, **fields: Any) -> JSON:
        return self._request("POST", "/v1/projects", json={"name": name, **fields})

    def list_projects(self) -> list[JSON]:
        return list(self._request("GET", "/v1/projects")["projects"])

    def create_conversation(self, **fields: Any) -> JSON:
        return self._request("POST", "/v1/conversations", json=fields)

    def get_conversation(self, conversation_id: str) -> JSON:
        return self._request("GET", f"/v1/conversations/{conversation_id}")

    def list_conversations(self, **params: Any) -> list[JSON]:
        return list(self._request("GET", "/v1/conversations", params=params)["conversations"])

    def turns(self, conversation_id: str, *, after_seq: int | None = None) -> list[JSON]:
        params = {"after_seq": after_seq} if after_seq is not None else None
        r = self._request("GET", f"/v1/conversations/{conversation_id}/turns", params=params)
        return list(r["turns"])

    # -- chat ----------------------------------------------------------------------------------

    def chat(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        model: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        body = _chat_body(message, conversation_id, project_id, model, None, extra)
        return ChatResult.from_json(self._request("POST", "/v1/chat", json=body))

    def chat_stream(
        self,
        message: str | None = None,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        messages: list[JSON] | None = None,
        model: str | None = None,
        **extra: Any,
    ) -> Iterator[StreamEvent]:
        body = _chat_body(message, conversation_id, project_id, model, messages, extra)
        return self._stream("POST", "/v1/chat/stream", body)

    def cancel_stream(self, stream_id: str) -> JSON:
        return self._request("POST", f"/v1/chat/stream/{stream_id}:cancel")

    def stream_summary(self, stream_id: str) -> JSON:
        return self._request("GET", f"/v1/chat/stream/{stream_id}")

    def resume_stream(self, stream_id: str, delivered_until_seq: int) -> Iterator[StreamEvent]:
        return self._stream(
            "POST",
            f"/v1/chat/stream/{stream_id}:resume",
            {"delivered_until_seq": delivered_until_seq},
        )

    # -- OpenAI-compatible ------------------------------------------------------------------------

    def completions(self, model: str, messages: list[JSON], **extra: Any) -> Completion:
        r = self._request(
            "POST", "/v1/chat/completions", json={"model": model, "messages": messages, **extra}
        )
        return Completion.from_json(r)

    # -- upstream-compatible ----------------------------------------------------------------------

    def execute(self, ask: str, **extra: Any) -> JSON:
        return self._request("POST", "/v1/execute", json={"ask": ask, **extra})

    def execution(self, execution_id: str) -> JSON:
        return self._request("GET", f"/v1/executions/{execution_id}")


class AsyncChatEngine(_Base):
    """Asynchronous client (same surface; streams are async iterators)."""

    def __init__(self, base_url: str = "http://localhost:8080", **kw: Any) -> None:
        transport = kw.pop("transport", None)
        super().__init__(base_url, **kw)
        self._http = httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers, timeout=self.timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncChatEngine:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> JSON:
        last: ChatEngineAPIError | None = None
        for attempt in range(self.max_retries + 1):
            resp = await self._http.request(method, path, **kw)
            if resp.status_code in RETRYABLE and attempt < self.max_retries:
                try:
                    self._raise(resp)
                except ChatEngineAPIError as e:
                    last = e
                await asyncio.sleep(_backoff(attempt, last, resp.headers))
                continue
            self._final(resp, attempt)
            return {} if resp.status_code == 204 else dict(resp.json())
        raise RetryExhausted(last.status if last else 503, last.body if last else {})

    async def _stream(self, method: str, path: str, body: JSON) -> AsyncIterator[StreamEvent]:
        async with self._http.stream(
            method, path, json=body, headers={"accept": "text/event-stream"}
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                self._raise(resp)
            buf: list[str] = []
            async for line in resp.aiter_lines():
                buf.append(line)
                if line.strip() == "":
                    for ev in parse_sse(buf):
                        yield ev
                    buf = []

    async def health(self) -> JSON:
        return await self._request("GET", "/healthz")

    async def models(self) -> list[JSON]:
        return list((await self._request("GET", "/v1/openai/models"))["data"])

    async def create_project(self, name: str, **fields: Any) -> JSON:
        return await self._request("POST", "/v1/projects", json={"name": name, **fields})

    async def create_conversation(self, **fields: Any) -> JSON:
        return await self._request("POST", "/v1/conversations", json=fields)

    async def turns(self, conversation_id: str) -> list[JSON]:
        r = await self._request("GET", f"/v1/conversations/{conversation_id}/turns")
        return list(r["turns"])

    async def chat(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        model: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        body = _chat_body(message, conversation_id, project_id, model, None, extra)
        return ChatResult.from_json(await self._request("POST", "/v1/chat", json=body))

    def chat_stream(
        self,
        message: str | None = None,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        messages: list[JSON] | None = None,
        model: str | None = None,
        **extra: Any,
    ) -> AsyncIterator[StreamEvent]:
        body = _chat_body(message, conversation_id, project_id, model, messages, extra)
        return self._stream("POST", "/v1/chat/stream", body)

    async def completions(self, model: str, messages: list[JSON], **extra: Any) -> Completion:
        r = await self._request(
            "POST", "/v1/chat/completions", json={"model": model, "messages": messages, **extra}
        )
        return Completion.from_json(r)

    async def execute(self, ask: str, **extra: Any) -> JSON:
        return await self._request("POST", "/v1/execute", json={"ask": ask, **extra})
