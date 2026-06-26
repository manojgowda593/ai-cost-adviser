"""
progress.py — WebSocket live-progress hub (step ④ of the request flow).

The frontend connects to ws://localhost:8000/ws/progress/{analysis_id} and
listens. During POST /api/analyze, the backend calls push(analysis_id, msg)
at each stage; every socket subscribed to that analysis_id receives it.

Why a hub keyed by analysis_id: multiple analyses can run at once, and the
client may connect slightly before or after a stage fires. We buffer messages
per analysis so a socket that connects mid-flight still receives the backlog,
then live updates.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Set

from fastapi import WebSocket


class ProgressHub:
    # Keep backlogs for the most recent N analyses so a client that connects
    # AFTER the analysis finished (e.g. the browser opens the socket right after
    # /api/analyze returns the id) still replays the full step list. Bounded so
    # memory can't grow without limit.
    MAX_BACKLOGS = 200

    def __init__(self) -> None:
        # analysis_id -> set of connected sockets
        self._subscribers: Dict[str, Set[WebSocket]] = {}
        # analysis_id -> ordered list of messages already sent (for late joiners).
        # Insertion order = age, so we can evict the oldest when over the cap.
        self._backlog: Dict[str, List[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, analysis_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._subscribers.setdefault(analysis_id, set()).add(ws)
            backlog = list(self._backlog.get(analysis_id, []))
        # Replay anything that already happened before this socket connected.
        for msg in backlog:
            await ws.send_json({"analysis_id": analysis_id, "message": msg})

    async def disconnect(self, analysis_id: str, ws: WebSocket) -> None:
        async with self._lock:
            subs = self._subscribers.get(analysis_id)
            if subs:
                subs.discard(ws)
                if not subs:
                    self._subscribers.pop(analysis_id, None)

    async def push(self, analysis_id: str, message: str) -> None:
        """Send a progress message to all sockets on this analysis, and buffer it."""
        async with self._lock:
            self._backlog.setdefault(analysis_id, []).append(message)
            # Evict oldest backlogs if we're over the cap (but never the one we
            # just appended to).
            while len(self._backlog) > self.MAX_BACKLOGS:
                oldest = next(iter(self._backlog))
                if oldest == analysis_id:
                    break
                self._backlog.pop(oldest, None)
            targets = list(self._subscribers.get(analysis_id, []))
        dead: List[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json({"analysis_id": analysis_id, "message": message})
            except Exception:
                dead.append(ws)
        if dead:
            for ws in dead:
                await self.disconnect(analysis_id, ws)

    def clear(self, analysis_id: str) -> None:
        """
        Explicit backlog drop. NOT called automatically on completion — we keep
        the backlog so a late-connecting client still replays the full sequence;
        old backlogs are evicted by the MAX_BACKLOGS cap in push() instead.
        """
        self._backlog.pop(analysis_id, None)


# A single shared hub for the app.
hub = ProgressHub()
