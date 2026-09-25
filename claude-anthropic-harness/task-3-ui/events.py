"""Event bus: every record goes to the run log first, then to the UI.

One path for both, so the UI never shows something the log does not hold.
Records are the dicts RunLog.write returns (with seq/ts/kind), already JSON-safe.
History is kept in memory so a reloaded page, or a reconnecting EventSource,
can replay from any seq.
"""

import asyncio
import threading
from typing import Any

from runlog import RunLog


class EventBus:
    def __init__(self, log: RunLog):
        self.log = log
        self.history: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()

    def publish(self, kind: str, **fields: Any) -> None:
        # SDK callbacks (e.g. stderr) are not guaranteed to run on the loop thread.
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(lambda: self._publish(kind, fields))
        else:
            self._publish(kind, fields)

    def _publish(self, kind: str, fields: dict[str, Any]) -> None:
        record = self.log.write(kind, **fields)
        self.history.append(record)
        for q in list(self._subscribers):
            q.put_nowait(record)

    def subscribe(self, after: int = 0) -> tuple[list[dict[str, Any]], asyncio.Queue]:
        """Backlog (seq > after) plus a queue for everything newer.

        No await between copying the backlog and registering the queue, so
        nothing can be published in between.
        """
        backlog = [r for r in self.history if r["seq"] > after]
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return backlog, q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def close_streams(self) -> None:
        """Tell every open stream to finish (sentinel None), e.g. at shutdown."""
        for q in list(self._subscribers):
            q.put_nowait(None)
