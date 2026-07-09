"""Async tool cache with TTL, LRU eviction, and single-flight dedup.

Single-flight pattern: concurrent callers with the same key share ONE inflight
backend call. First caller starts the work, subsequent callers await the same
future. If the backend raises, ALL waiters see the same exception.

This prevents the thundering-herd problem when Month 5 parallel workers
call get_service_owner("checkout-api") simultaneously.

Design points:
- Inflight futures are keyed by the same key the cache uses.
- The lock is only held around inflight-registry mutations (microsecond scale).
- The actual backend call runs OUTSIDE the lock.
- ttl_seconds=0 means single-flight only, no cache store population.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from time import monotonic
from typing import Any, Awaitable, Callable, Hashable


class ToolCache:
    """TTL + LRU cache with async single-flight dedup.

    Args:
        ttl_by_tool: Mapping of tool_name -> ttl_seconds.
                     A TTL of 0 means single-flight only (no cache store).
        max_entries: Maximum number of cached entries (LRU eviction).
        single_flight: Whether to enable concurrent-call dedup.
    """

    def __init__(
        self,
        ttl_by_tool: dict[str, int],
        max_entries: int = 512,
        single_flight: bool = True,
    ) -> None:
        self._ttl_by_tool = ttl_by_tool
        self._max = max_entries
        self._store: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Future[Any]] = {}
        self._single_flight = single_flight
        self._lock = asyncio.Lock()

    async def get_or_call(
        self,
        key: Hashable,
        fn: Callable[[], Awaitable[Any]],
        tool: str,
    ) -> Any:
        """Get a value from cache, or call the backend function.

        If single_flight is enabled and another coroutine is already fetching
        the same key, this call awaits the existing future instead of making
        a duplicate backend request.

        Args:
            key: Cache key (must be hashable). Typically (tool_name, *args).
            fn: Async callable that produces the value on cache miss.
            tool: Tool name for TTL lookup.

        Returns:
            The cached or freshly-computed value.

        Raises:
            Any exception from fn() propagates to all waiters.
        """
        ttl = self._ttl_by_tool.get(tool, 0)
        now = monotonic()

        # Cache hit path (safe without lock in asyncio's cooperative model)
        if ttl > 0:
            cached = self._store.get(key)
            if cached is not None and (now - cached[0]) < ttl:
                self._store.move_to_end(key)
                return cached[1]

        # Miss. Check single-flight — is another coroutine already fetching?
        if self._single_flight:
            async with self._lock:
                inflight = self._inflight.get(key)
                if inflight is not None:
                    # Someone else is fetching. Await their result.
                    pass
                else:
                    # We are the first. Register our future BEFORE releasing lock.
                    loop = asyncio.get_running_loop()
                    fut: asyncio.Future[Any] = loop.create_future()
                    self._inflight[key] = fut
                    inflight = None

            if inflight is not None:
                # Wait for the other coroutine's result
                return await inflight

            # We own this flight. Execute the backend call.
            try:
                value = await fn()
                # Resolve the future for all waiters
                fut.set_result(value)
            except BaseException as e:
                fut.set_exception(e)
                raise
            finally:
                async with self._lock:
                    self._inflight.pop(key, None)
        else:
            # No single-flight: just call directly
            value = await fn()

        # Populate cache store (only if TTL > 0)
        if ttl > 0:
            self._store[key] = (monotonic(), value)
            self._store.move_to_end(key)
            # LRU eviction
            while len(self._store) > self._max:
                self._store.popitem(last=False)

        return value

    def invalidate(self, key: Hashable) -> None:
        """Remove a specific key from the cache."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()

    @property
    def size(self) -> int:
        """Current number of cached entries."""
        return len(self._store)
