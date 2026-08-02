"""Клиент Core→Runner поверх UDS (Master Spec §13)."""

from __future__ import annotations

import asyncio
from typing import Callable

from .protocol import decode, encode


class RunnerClient:
    def __init__(self, socket_path: str, token: str):
        self.socket_path = socket_path
        self.token = token

    async def ping(self) -> dict:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(encode({"type": "ping", "token": self.token}))
        await writer.drain()
        line = await reader.readline()
        writer.close()
        return decode(line) if line else {}

    async def run(self, request: dict, *, on_event: Callable[[dict], None] | None = None,
                  timeout_s: float = 120.0) -> list[dict]:
        """Выполнить запрос, вернуть список событий (последнее — run.finished)."""

        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(encode({"type": "run", "token": self.token, "request": request}))
        await writer.drain()
        events: list[dict] = []

        async def collect():
            while True:
                line = await reader.readline()
                if not line:
                    break
                evt = decode(line)
                events.append(evt)
                if on_event:
                    on_event(evt)
                if evt.get("type") in ("run.finished", "error"):
                    break

        await asyncio.wait_for(collect(), timeout=timeout_s)
        writer.close()
        return events

    async def interrupt(self, request_id: str) -> dict:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(encode({"type": "interrupt", "token": self.token, "request_id": request_id}))
        await writer.drain()
        line = await reader.readline()
        writer.close()
        return decode(line) if line else {}
