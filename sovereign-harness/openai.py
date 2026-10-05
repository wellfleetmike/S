"""
Drop-in openai shim using httpx.
Buffers SSE streaming lines and aggregates incremental tool-call tokens.
"""

from __future__ import annotations
import json
import httpx


class _Choice:
    def __init__(self, delta=None, finish_reason=None):
        self.delta = delta or _Delta()
        self.finish_reason = finish_reason


class _Delta:
    def __init__(self, content=None, tool_calls=None, role=None):
        self.content = content
        self.tool_calls = tool_calls
        self.role = role


class _ToolCallFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, index=0, id=None, function=None, type="function"):
        self.index = index
        self.id = id
        self.function = function or _ToolCallFunction()
        self.type = type


class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _ChatCompletionChunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


def _parse_chunk(data: dict) -> _ChatCompletionChunk:
    choices = []
    for c in data.get("choices", []):
        d = c.get("delta", {})
        tool_calls = None
        if "tool_calls" in d:
            tool_calls = []
            for tc in d["tool_calls"]:
                fn = tc.get("function", {})
                tool_calls.append(
                    _ToolCall(
                        index=tc.get("index", 0),
                        id=tc.get("id"),
                        function=_ToolCallFunction(
                            name=fn.get("name"),
                            arguments=fn.get("arguments"),
                        ),
                    )
                )
        delta = _Delta(
            content=d.get("content"),
            tool_calls=tool_calls,
            role=d.get("role"),
        )
        choices.append(_Choice(delta=delta, finish_reason=c.get("finish_reason")))

    usage = None
    if "usage" in data and data["usage"]:
        u = data["usage"]
        usage = _Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )

    return _ChatCompletionChunk(choices=choices, usage=usage)


class _StreamIterator:
    def __init__(self, http_client: httpx.Client, body: dict):
        self._http = http_client
        self._body = body
        self._response = None

    def _connect(self):
        if self._response is None:
            self._response = self._http.send(
                self._http.build_request("POST", "/v1/chat/completions", json=self._body),
                stream=True,
            )
            self._response.raise_for_status()

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *args):
        if self._response:
            self._response.close()
            self._response = None

    def _iter_sse(self):
        buffer = ""
        for raw in self._response.iter_text():
            buffer += raw
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                yield _parse_chunk(data)

    def __iter__(self):
        self._connect()
        try:
            yield from self._iter_sse()
        finally:
            if self._response:
                self._response.close()
                self._response = None

    def __del__(self):
        if self._response:
            try:
                self._response.close()
            except Exception:
                pass


class _Completions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        stream = kwargs.pop("stream", False)
        body = {k: v for k, v in kwargs.items() if v is not None}
        body["stream"] = stream

        if stream:
            return _StreamIterator(self._client._http, body)
        else:
            resp = self._client._http.post(
                "/v1/chat/completions",
                json=body,
                timeout=300.0,
            )
            resp.raise_for_status()
            return _parse_chunk(resp.json())


class _Chat:
    def __init__(self, client):
        self.completions = _Completions(client)


class OpenAI:
    def __init__(self, api_key: str = "dummy", base_url: str = "http://127.0.0.1:11434/v1"):
        base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300.0,
        )
        self.chat = _Chat(self)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
