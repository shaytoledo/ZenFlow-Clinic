"""An offline Telegram Bot API for tests (Phase 7.1).

One fake answers both ways the app talks to Telegram, so the adapter's real encoding and error
handling run in every test:

* `transport()` — an httpx transport for `TelegramChannel(token=…)` (web process, queued jobs);
* `request()`   — a python-telegram-bot `BaseRequest` for a real `telegram.Bot` (bot process).

Every call is recorded as an `ApiCall`; nested JSON objects are decoded, so tests compare dicts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any

import httpx
from telegram.error import NetworkError
from telegram.request import BaseRequest, RequestData

API_HOST = "api.telegram.org"


@dataclass
class ApiCall:
    bot: str  # the label of the token ("patient", "therapist") or the token itself
    method: str
    params: dict[str, Any]
    files: dict[str, tuple[str | None, bytes]] = field(default_factory=dict)
    ok: bool = True  # False when the fake answered with an error


@dataclass
class _Failure:
    status: int
    description: str
    retry_after: int | None = None
    bot: str | None = None  # only the next call by this bot fails; None = the next call


class FakeBotApi:
    BOT_USER = {"id": 1_000_000_000, "is_bot": True, "first_name": "ZenFlow", "username": "zf_bot"}

    def __init__(self, labels: dict[str, str] | None = None) -> None:
        self.labels = labels or {}
        self.api_calls: list[ApiCall] = []
        self.down = False  # True = the network fails before Telegram answers
        self._failures: list[_Failure] = []
        self._next_id = 100

    # ── scripting ──
    def fail_next(
        self,
        description: str = "Forbidden: bot was blocked by the user",
        *,
        status: int = 403,
        retry_after: int | None = None,
        times: int = 1,
        bot: str | None = None,
    ) -> None:
        self._failures.extend(_Failure(status, description, retry_after, bot) for _ in range(times))

    def of(self, method: str) -> list[ApiCall]:
        return [c for c in self.api_calls if c.method == method]

    # ── the API ──
    def answer(
        self, token: str, method: str, params: dict[str, Any], files: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        call = ApiCall(self.labels.get(token, token), method, params, files)
        self.api_calls.append(call)
        f = next((f for f in self._failures if f.bot in (None, call.bot)), None)
        if f is not None:
            self._failures.remove(f)
            call.ok = False
            body: dict[str, Any] = {
                "ok": False,
                "error_code": f.status,
                "description": f.description,
            }
            if f.retry_after is not None:
                body["parameters"] = {"retry_after": f.retry_after}
            return f.status, body
        result: Any
        if method == "getMe":
            result = self.BOT_USER
        elif method == "sendChatAction":
            result = True
        else:
            if method == "editMessageText":
                message_id = int(params.get("message_id") or 0)
            else:
                self._next_id += 1
                message_id = self._next_id
            result = {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": _chat_id(params.get("chat_id")), "type": "private"},
            }
            if "text" in params:
                result["text"] = params["text"]
        return 200, {"ok": True, "result": result}

    # ── transports ──
    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("connection refused", request=request)
            assert request.url.host == API_HOST, request.url
            token, method = _split(request.url.path)
            params, files = _decode(request)
            status, body = self.answer(token, method, params, files)
            return httpx.Response(status, json=body)

        return httpx.MockTransport(handle)

    def request(self) -> BaseRequest:
        return _PtbRequest(self)


class _PtbRequest(BaseRequest):
    def __init__(self, api: FakeBotApi) -> None:
        self.api = api

    @property
    def read_timeout(self) -> float:
        return 5.0

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def do_request(  # type: ignore[override]
        self, url: str, method: str, request_data: RequestData | None = None, **_: Any
    ) -> tuple[int, bytes]:
        if self.api.down:
            raise NetworkError("connection refused")
        token, api_method = _split(httpx.URL(url).path)
        params = dict(request_data.parameters) if request_data else {}
        files: dict[str, tuple[str | None, bytes]] = {}
        if request_data and request_data.contains_files:
            for name, part in request_data.multipart_data.items():
                if isinstance(part, tuple):
                    content = part[1]
                    data = content if isinstance(content, bytes) else content.read()
                    files[name] = (part[0], data)
            params = {k: v for k, v in params.items() if k not in files}
        status, body = self.api.answer(token, api_method, params, files)
        return status, json.dumps(body).encode()


def _split(path: str) -> tuple[str, str]:
    _, bot_token, method = path.split("/", 2)
    assert bot_token.startswith("bot"), path
    return bot_token[3:], method


def _chat_id(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _decode(request: httpx.Request) -> tuple[dict[str, Any], dict[str, Any]]:
    ctype = request.headers.get("content-type", "")
    body = request.content
    if not body:
        return {}, {}
    if ctype.startswith("application/json"):
        return json.loads(body), {}
    if ctype.startswith("multipart/form-data"):
        message = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body
        )
        params: dict[str, Any] = {}
        files: dict[str, tuple[str | None, bytes]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            raw = part.get_payload(decode=True)
            payload = raw if isinstance(raw, bytes) else b""
            filename = part.get_filename()
            if filename is not None:
                files[str(name)] = (filename, payload)
            elif name == "chat_id":
                params["chat_id"] = _chat_id(payload.decode())
            else:
                params[str(name)] = _maybe_json(payload.decode())
        return params, files
    raise AssertionError(f"unexpected Telegram request body: {ctype}")
