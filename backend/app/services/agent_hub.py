"""Connected client scanner agents.

The API runs as a single uvicorn process, so this in-memory registry is
the source of truth. Scan endpoints are sync and run in the threadpool;
`request()` hands the call to the event loop that owns the agent's socket.
"""

import asyncio
import concurrent.futures
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 120
ID_LEN = 16
AGENT_VERSION = "0.1.0"


class AgentOffline(Exception):
    pass


class AgentTimeout(Exception):
    pass


@dataclass
class EsclResponse:
    status: int
    content_type: str
    headers: dict[str, str]
    body: bytes


@dataclass
class _Pending:
    future: asyncio.Future
    meta: dict | None = None
    chunks: list[bytes] = field(default_factory=list)


@dataclass
class _Agent:
    ws: WebSocket
    loop: asyncio.AbstractEventLoop
    scanners: list[dict] = field(default_factory=list)
    pending: dict[str, _Pending] = field(default_factory=dict)
    closed: bool = False


class AgentHub:
    def __init__(self) -> None:
        self._tokens: dict[str, tuple[int, str, float]] = {}
        self._agents: dict[tuple[int, str], _Agent] = {}
        self._lock = threading.Lock()

    # tokens ---------------------------------------------------------------

    def issue_token(self, user_id: int, client_id: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens = {t: v for t, v in self._tokens.items() if v[2] > now}
            self._tokens[token] = (user_id, client_id, now + TOKEN_TTL_SECONDS)
        return token

    def consume_token(self, token: str, now: float | None = None) -> tuple[int, str] | None:
        now = time.time() if now is None else now
        with self._lock:
            entry = self._tokens.pop(token, None)
        if entry is None or entry[2] <= now:
            return None
        return entry[0], entry[1]

    # registry -------------------------------------------------------------

    def connected(self, user_id: int, client_id: str) -> bool:
        with self._lock:
            return (user_id, client_id) in self._agents

    def scanners(self, user_id: int, client_id: str) -> list[dict]:
        with self._lock:
            agent = self._agents.get((user_id, client_id))
            return list(agent.scanners) if agent else []

    async def serve(self, ws: WebSocket, user_id: int, client_id: str) -> None:
        key = (user_id, client_id)
        agent = _Agent(ws=ws, loop=asyncio.get_running_loop())
        with self._lock:
            old = self._agents.get(key)
            self._agents[key] = agent
        try:
            if old is not None:
                old.closed = True
                self._fail_pending(old)
                try:
                    await old.ws.close(code=4000)
                except Exception:  # noqa: BLE001 - old socket may be dead already
                    pass
            await ws.send_json({"type": "welcome", "resume_token": self.issue_token(user_id, client_id)})
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                try:
                    if msg.get("bytes") is not None:
                        self._on_chunk(agent, msg["bytes"])
                    elif msg.get("text") is not None:
                        self._on_message(agent, json.loads(msg["text"]))
                except (ValueError, KeyError, TypeError, AttributeError):
                    log.warning("malformed message from agent %s", key, exc_info=True)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            agent.closed = True
            with self._lock:
                if self._agents.get(key) is agent:
                    del self._agents[key]
            self._fail_pending(agent)

    def _on_message(self, agent: _Agent, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            if msg.get("version") != AGENT_VERSION:
                log.warning("agent version %s, server expects %s", msg.get("version"), AGENT_VERSION)
        elif kind == "devices":
            agent.scanners = [
                {"uuid": str(d["uuid"]), "name": str(d.get("name") or d["uuid"])}
                for d in msg.get("devices") or []
            ]
        elif kind == "escl_response":
            pending = agent.pending.get(msg.get("id", ""))
            if pending is not None:
                pending.meta = msg
        elif kind == "end":
            pending = agent.pending.pop(msg.get("id", ""), None)
            if pending is not None and not pending.future.done():
                meta = pending.meta or {}
                pending.future.set_result(
                    EsclResponse(
                        status=int(meta.get("status", 502)),
                        content_type=str(meta.get("content_type", "")),
                        headers=dict(meta.get("headers") or {}),
                        body=b"".join(pending.chunks),
                    )
                )

    def _on_chunk(self, agent: _Agent, data: bytes) -> None:
        pending = agent.pending.get(data[:ID_LEN].decode(errors="replace"))
        if pending is not None:
            pending.chunks.append(data[ID_LEN:])

    @staticmethod
    def _fail_pending(agent: _Agent) -> None:
        def fail() -> None:
            for pending in agent.pending.values():
                if not pending.future.done():
                    pending.future.set_exception(AgentOffline())
            agent.pending.clear()

        try:
            if asyncio.get_running_loop() is agent.loop:
                fail()
                return
        except RuntimeError:
            pass
        agent.loop.call_soon_threadsafe(fail)

    # requests -------------------------------------------------------------

    def request(
        self,
        user_id: int,
        client_id: str,
        scanner_uuid: str,
        method: str,
        path: str,
        body: str | None = None,
        timeout: float = 120.0,
    ) -> EsclResponse:
        with self._lock:
            agent = self._agents.get((user_id, client_id))
        if agent is None:
            raise AgentOffline()
        rid = secrets.token_hex(ID_LEN // 2)

        async def call() -> EsclResponse:
            if agent.closed:
                raise AgentOffline()
            future = agent.loop.create_future()
            try:
                agent.pending[rid] = _Pending(future=future)
                await agent.ws.send_json(
                    {"type": "escl", "id": rid, "scanner_uuid": scanner_uuid,
                     "method": method, "path": path, "body": body or ""}
                )
                return await asyncio.wait_for(future, timeout)
            finally:
                agent.pending.pop(rid, None)

        try:
            return asyncio.run_coroutine_threadsafe(call(), agent.loop).result(timeout + 5)
        except (asyncio.TimeoutError, TimeoutError, concurrent.futures.TimeoutError):
            raise AgentTimeout()
        except (RuntimeError, WebSocketDisconnect):
            raise AgentOffline()  # socket closed while sending


_hub = AgentHub()


def get_agent_hub() -> AgentHub:
    return _hub
