"""Tests for sentinel.tools.cache.ToolCache.

Time is controlled by monkeypatching the module-level `monotonic` name
(cache.py does `from time import monotonic`) rather than sleeping.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

import sentinel.tools.cache as cache_mod
from sentinel.tools.cache import ToolCache


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(cache_mod, "monotonic", clock)
    return clock


def _counting_fn(
    counter: list[int], return_value: object = "value"
) -> Callable[[], Awaitable[object]]:
    async def fn() -> object:
        counter[0] += 1
        return return_value

    return fn


async def test_ttl_hit_returns_cached_value_without_recalling(fake_clock: FakeClock) -> None:
    cache = ToolCache(ttl_by_tool={"ownership": 10})
    calls = [0]
    fn = _counting_fn(calls)

    first = await cache.get_or_call(("k",), fn, tool="ownership")
    second = await cache.get_or_call(("k",), fn, tool="ownership")

    assert first == second == "value"
    assert calls[0] == 1


async def test_ttl_expiry_triggers_fresh_call(fake_clock: FakeClock) -> None:
    cache = ToolCache(ttl_by_tool={"ownership": 10})
    calls = [0]
    fn = _counting_fn(calls)

    await cache.get_or_call(("k",), fn, tool="ownership")
    fake_clock.advance(11)
    await cache.get_or_call(("k",), fn, tool="ownership")

    assert calls[0] == 2


async def test_ttl_zero_means_no_store_reuse_across_sequential_calls(
    fake_clock: FakeClock,
) -> None:
    cache = ToolCache(ttl_by_tool={"past_incidents": 0})
    calls = [0]
    fn = _counting_fn(calls)

    await cache.get_or_call(("k",), fn, tool="past_incidents")
    await cache.get_or_call(("k",), fn, tool="past_incidents")

    assert calls[0] == 2


async def test_single_flight_dedupes_concurrent_calls_with_same_key() -> None:
    cache = ToolCache(ttl_by_tool={"x": 0}, single_flight=True)
    calls = [0]

    async def fn() -> str:
        calls[0] += 1
        await asyncio.sleep(0.01)
        return "shared-value"

    results = await asyncio.gather(*[cache.get_or_call(("k",), fn, tool="x") for _ in range(5)])

    assert calls[0] == 1
    assert results == ["shared-value"] * 5


async def test_single_flight_fans_out_exception_to_all_waiters() -> None:
    cache = ToolCache(ttl_by_tool={"x": 0}, single_flight=True)
    calls = [0]

    async def fn() -> None:
        calls[0] += 1
        await asyncio.sleep(0.01)
        raise ValueError("backend broke")

    results = await asyncio.gather(
        *[cache.get_or_call(("k",), fn, tool="x") for _ in range(5)],
        return_exceptions=True,
    )

    assert calls[0] == 1
    assert all(isinstance(r, ValueError) for r in results)
    assert all(str(r) == "backend broke" for r in results)


async def test_single_flight_disabled_calls_backend_for_each_concurrent_caller() -> None:
    cache = ToolCache(ttl_by_tool={"x": 0}, single_flight=False)
    calls = [0]

    async def fn() -> int:
        calls[0] += 1
        await asyncio.sleep(0.01)
        return calls[0]

    await asyncio.gather(*[cache.get_or_call(("k",), fn, tool="x") for _ in range(3)])

    assert calls[0] == 3


async def test_lru_eviction_drops_oldest_entry_past_max_entries(
    fake_clock: FakeClock,
) -> None:
    cache = ToolCache(ttl_by_tool={"x": 100}, max_entries=2)
    calls = [0]
    fn = _counting_fn(calls)

    await cache.get_or_call(("a",), fn, tool="x")
    await cache.get_or_call(("b",), fn, tool="x")
    await cache.get_or_call(("c",), fn, tool="x")

    assert cache.size == 2

    calls_before = calls[0]
    await cache.get_or_call(("a",), fn, tool="x")  # evicted -> miss

    assert calls[0] == calls_before + 1


async def test_lru_touch_on_hit_protects_recently_used_entry(
    fake_clock: FakeClock,
) -> None:
    cache = ToolCache(ttl_by_tool={"x": 100}, max_entries=2)
    calls = [0]
    fn = _counting_fn(calls)

    await cache.get_or_call(("a",), fn, tool="x")
    await cache.get_or_call(("b",), fn, tool="x")
    await cache.get_or_call(("a",), fn, tool="x")  # touch "a", "b" now oldest
    await cache.get_or_call(("c",), fn, tool="x")  # evicts "b", not "a"

    calls_before = calls[0]
    await cache.get_or_call(("a",), fn, tool="x")

    assert calls[0] == calls_before  # still cached, no new call


async def test_invalidate_removes_specific_key(fake_clock: FakeClock) -> None:
    cache = ToolCache(ttl_by_tool={"x": 100})
    calls = [0]
    fn = _counting_fn(calls)

    await cache.get_or_call(("a",), fn, tool="x")
    cache.invalidate(("a",))
    await cache.get_or_call(("a",), fn, tool="x")

    assert calls[0] == 2


async def test_clear_removes_all_entries(fake_clock: FakeClock) -> None:
    cache = ToolCache(ttl_by_tool={"x": 100})
    fn = _counting_fn([0])

    await cache.get_or_call(("a",), fn, tool="x")
    await cache.get_or_call(("b",), fn, tool="x")
    cache.clear()

    assert cache.size == 0
