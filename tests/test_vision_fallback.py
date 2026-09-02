"""Offline tests for the provider fallback chain in gemini_client.py.

Nothing here touches the network: the provider callables and the clock are
injected, so the whole suite is free and instant (the Google free tier is
20 requests/day and burns out if tests call it).
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Deterministic credentials regardless of what .env holds, and mock OFF so the
# tests exercise the real chain.
os.environ.setdefault("TABI_API_KEY", "test-tabi-key")
os.environ.setdefault("TABI_BASE_URL", "https://tabi.invalid/v1")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("APP_MODE", "development")
os.environ["MOCK_VISION"] = "0"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import gemini_client


class Clock:
    """Stand-in for time.monotonic/time.sleep so budget tests run instantly."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Response:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body (Cloudflare block page)")
        return self._json


def ok_reply(text):
    return {"choices": [{"message": {"content": text}}]}


def _boom(*args):
    raise RuntimeError("provider said no")


def swap(providers, calls, clock=None, budget=None, cap=None, min_slice=None, mock=None):
    """Install chain state and return a callable that restores it."""
    saved = (
        gemini_client.PROVIDERS,
        gemini_client._PROVIDER_CALLS,
        gemini_client.MOCK_VISION,
        gemini_client._CHAIN_BUDGET_SECONDS,
        gemini_client._PROVIDER_CAP_SECONDS,
        gemini_client._MIN_PROVIDER_SLICE_SECONDS,
        gemini_client.time,
    )
    gemini_client.PROVIDERS = list(providers)
    gemini_client._PROVIDER_CALLS = dict(calls)
    if mock is not None:
        gemini_client.MOCK_VISION = mock
    if budget is not None:
        gemini_client._CHAIN_BUDGET_SECONDS = budget
    if cap is not None:
        gemini_client._PROVIDER_CAP_SECONDS = cap
    if min_slice is not None:
        gemini_client._MIN_PROVIDER_SLICE_SECONDS = min_slice
    if clock is not None:
        gemini_client.time = clock

    def restore():
        (
            gemini_client.PROVIDERS,
            gemini_client._PROVIDER_CALLS,
            gemini_client.MOCK_VISION,
            gemini_client._CHAIN_BUDGET_SECONDS,
            gemini_client._PROVIDER_CAP_SECONDS,
            gemini_client._MIN_PROVIDER_SLICE_SECONDS,
            gemini_client.time,
        ) = saved

    return restore


def recorder(name, result=None, error=None, burn=0.0, calls=None):
    def call(prompt, image_data, mime_type, timeout):
        if calls is not None:
            calls.append((name, timeout))
        if burn:
            gemini_client.time.now += burn
        if error is not None:
            raise error
        return result

    return call


CHECKS = []


def check(name):
    def decorate(fn):
        CHECKS.append((name, fn))
        return fn

    return decorate


@check("primary provider answers, secondary is never called")
def t_primary_wins():
    calls = []
    restore = swap(
        ["tabi", "google"],
        {
            "tabi": recorder("tabi", result="PKR\n500", calls=calls),
            "google": recorder("google", result="should not run", calls=calls),
        },
    )
    try:
        meta = {}
        assert gemini_client.generate("p", meta=meta) == "PKR\n500"
        assert meta["provider"] == "tabi", meta
        assert meta["mock"] is False
        assert meta["attempts"] == []
        assert [name for name, _ in calls] == ["tabi"], calls
    finally:
        restore()


@check("falls back to the secondary when the primary fails")
def t_fallback_on_error():
    calls = []
    restore = swap(
        ["tabi", "google"],
        {
            "tabi": recorder("tabi", error=RuntimeError("TabiAI HTTP 503"), calls=calls),
            "google": recorder("google", result="Panadol 500mg", calls=calls),
        },
    )
    try:
        meta = {}
        assert gemini_client.generate("p", meta=meta) == "Panadol 500mg"
        assert meta["provider"] == "google", meta
        assert len(meta["attempts"]) == 1, meta
        assert meta["attempts"][0]["provider"] == "tabi"
        assert meta["attempts"][0]["error"] == "RuntimeError"
        assert [name for name, _ in calls] == ["tabi", "google"], calls
    finally:
        restore()


@check("production ordering tries Google first")
def t_production_order():
    assert gemini_client.PROVIDERS[0] in ("tabi", "google")
    saved_mode = gemini_client.APP_MODE
    gemini_client.APP_MODE = "production"
    try:
        ordered = gemini_client._available_providers()
    finally:
        gemini_client.APP_MODE = saved_mode
    assert ordered[0] == "google", ordered
    assert "tabi" in ordered, ordered


@check("every provider failing raises VisionUnavailable, not a crash")
def t_all_fail():
    restore = swap(
        ["tabi", "google"],
        {
            "tabi": recorder("tabi", error=RuntimeError("TabiAI HTTP 403")),
            "google": recorder("google", error=RuntimeError("429 RESOURCE_EXHAUSTED")),
        },
    )
    try:
        meta = {}
        try:
            gemini_client.generate("p", meta=meta)
        except gemini_client.VisionUnavailable as error:
            assert "tabi" in str(error) and "google" in str(error), str(error)
        else:
            raise AssertionError("expected VisionUnavailable")
        assert len(meta["attempts"]) == 2, meta
    finally:
        restore()


@check("mock serves only as a last resort, and says so")
def t_mock_last_resort():
    restore = swap(
        ["tabi", "google"],
        {
            "tabi": recorder("tabi", error=RuntimeError("TabiAI HTTP 503")),
            "google": recorder("google", error=RuntimeError("429 RESOURCE_EXHAUSTED")),
        },
        mock=True,
    )
    try:
        meta = {}
        reply = gemini_client.generate("p", meta=meta, task="medicine")
        assert reply.startswith("Panadol 500mg"), reply
        assert meta["mock"] is True, meta
        assert meta["provider"] == "mock", meta
        assert gemini_client.generate("p", task="currency") == "PKR\n500"
        label = gemini_client.generate("p", task="label").splitlines()
        assert len(label) == 2 and label[0] > label[1], label
    finally:
        restore()


@check("mock never answers while a real provider still works")
def t_mock_not_used_early():
    restore = swap(
        ["tabi"],
        {"tabi": recorder("tabi", result="PKR\n100")},
        mock=True,
    )
    try:
        meta = {}
        assert gemini_client.generate("p", meta=meta, task="currency") == "PKR\n100"
        assert meta["mock"] is False, meta
        assert meta["provider"] == "tabi", meta
    finally:
        restore()


@check("chain stays inside the budget: no provider starts without a usable slice")
def t_budget_skip():
    calls = []
    clock = Clock()
    # Budget 110s, cap 100s: the first provider eats 100s, leaving 10s, which
    # is below the 20s minimum, so the second must be skipped rather than start
    # a call gunicorn would kill mid-flight.
    restore = swap(
        ["tabi", "google"],
        {
            "tabi": recorder("tabi", error=RuntimeError("TabiAI HTTP 503"), burn=100.0, calls=calls),
            "google": recorder("google", result="should not run", calls=calls),
        },
        clock=clock,
        budget=110,
        cap=100,
        min_slice=20,
    )
    try:
        try:
            gemini_client.generate("p")
        except gemini_client.VisionUnavailable:
            pass
        else:
            raise AssertionError("expected VisionUnavailable")
        assert [name for name, _ in calls] == ["tabi"], calls
    finally:
        restore()


@check("each provider gets a capped slice of the remaining budget")
def t_slice_capping():
    calls = []
    restore = swap(
        ["tabi", "google"],
        {
            "tabi": recorder("tabi", error=RuntimeError("boom"), burn=100.0, calls=calls),
            "google": recorder("google", result="x", calls=calls),
        },
        clock=Clock(),
        budget=170,
        cap=100,
    )
    try:
        gemini_client.generate("p")
        timeouts = dict(calls)
        assert timeouts["tabi"] == 100, timeouts
        # tabi burned its whole 100s cap, so google must get the 70s left over,
        # not a fresh cap-sized slice.
        assert timeouts["google"] == 70, timeouts
        assert timeouts["tabi"] + timeouts["google"] <= 170, timeouts
    finally:
        restore()


@check("TabiAI transport: 200 returns text, 503 retries, block page raises")
def t_tabi_transport():
    saved_post = gemini_client.requests.post
    saved_sleep = gemini_client.time.sleep
    posted = []
    gemini_client.time.sleep = lambda seconds: None
    try:
        def happy(*args, **kwargs):
            posted.append(kwargs.get("timeout"))
            return _Response(200, ok_reply("Panadol 500mg"))

        gemini_client.requests.post = happy
        assert gemini_client._generate_tabi("p", None, "image/jpeg", 30) == "Panadol 500mg"

        sequence = [_Response(503), _Response(200, ok_reply("PKR\n500"))]

        def flaky(*args, **kwargs):
            return sequence.pop(0)

        gemini_client.requests.post = flaky
        assert gemini_client._generate_tabi("p", None, "image/jpeg", 30) == "PKR\n500"
        assert sequence == [], "should have retried once and stopped"

        def blocked(*args, **kwargs):
            return _Response(403, None, text="<html>Attention Required!</html>")

        gemini_client.requests.post = blocked
        try:
            gemini_client._generate_tabi("p", None, "image/jpeg", 30)
        except RuntimeError as error:
            assert "unusable reply" in str(error), str(error)
        else:
            raise AssertionError("a Cloudflare block page must raise, not return HTML")

        def empty(*args, **kwargs):
            return _Response(200, ok_reply("   "))

        gemini_client.requests.post = empty
        try:
            gemini_client._generate_tabi("p", None, "image/jpeg", 30)
        except RuntimeError as error:
            assert "empty reply" in str(error), str(error)
        else:
            raise AssertionError("an empty reply must raise so the chain moves on")
    finally:
        gemini_client.requests.post = saved_post
        gemini_client.time.sleep = saved_sleep


@check("TabiAI never exceeds the slice it was given")
def t_tabi_respects_deadline():
    saved_post = gemini_client.requests.post
    saved_time = gemini_client.time
    clock = Clock()
    gemini_client.time = clock
    timeouts = []
    try:
        def always_503(*args, **kwargs):
            # Worst case for a real client: the call takes the whole timeout.
            granted = kwargs.get("timeout")
            timeouts.append(granted)
            clock.now += granted
            return _Response(503)

        gemini_client.requests.post = always_503
        try:
            gemini_client._generate_tabi("p", None, "image/jpeg", 100)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected a failure after the budget ran out")
        assert timeouts, "no attempt was made"
        assert all(t <= 100 for t in timeouts), timeouts
        assert clock.now <= 100, f"ran {clock.now}s of a 100s slice"
        # It must stop starting attempts once the window is too small to finish.
        assert timeouts[-1] >= gemini_client._MIN_ATTEMPT_SECONDS, timeouts
    finally:
        gemini_client.requests.post = saved_post
        gemini_client.time = saved_time


@check("a transport that ignores its timeout is abandoned on a wall clock")
def t_run_bounded_abandons():
    # Regression: TabiAI once ran 554s on a 100s requests timeout (its read
    # timer restarts on every byte received), ate the chain budget, and left
    # Google with negative time - so the scan failed even though Google was up.
    started = time.monotonic()
    try:
        gemini_client._run_bounded(time.sleep, (30,), 0.4, "stub")
    except TimeoutError as error:
        assert "stub" in str(error), str(error)
    else:
        raise AssertionError("expected TimeoutError once the slice ran out")
    assert time.monotonic() - started < 5, "the call was not actually cut off"

    assert gemini_client._run_bounded(lambda *a: "text", (), 5, "stub") == "text"
    try:
        gemini_client._run_bounded(_boom, (), 5, "stub")
    except RuntimeError as error:
        assert str(error) == "provider said no", str(error)
    else:
        raise AssertionError("the worker's own exception must reach the caller")


@check("a hung provider is cut off and the next one still answers")
def t_chain_survives_hung_provider():
    saved_grace = gemini_client._ABANDON_GRACE_SECONDS
    gemini_client._ABANDON_GRACE_SECONDS = 0.3
    restore = swap(
        ["tabi", "google"],
        {
            # Ignores the slice it was handed, exactly like the real transport.
            "tabi": lambda *args: time.sleep(30),
            "google": recorder("google", result="Panadol 500mg"),
        },
        clock=Clock(),
        budget=170,
        cap=0.2,
    )
    try:
        meta = {}
        assert gemini_client.generate("p", meta=meta) == "Panadol 500mg"
        assert meta["provider"] == "google", meta
        assert len(meta["attempts"]) == 1, meta
        assert meta["attempts"][0]["provider"] == "tabi", meta
        assert meta["attempts"][0]["error"] == "TimeoutError", meta
    finally:
        restore()
        gemini_client._ABANDON_GRACE_SECONDS = saved_grace


@check("health_summary names every provider in order")
def t_health_summary():
    summary = gemini_client.health_summary()
    assert "mode=" in summary and "providers=" in summary, summary
    assert "mock=off" in summary, summary
    restore = swap(["google", "tabi"], {})
    try:
        assert "providers=google+tabi" in gemini_client.health_summary()
    finally:
        restore()


def main():
    failures = []
    for name, fn in CHECKS:
        try:
            fn()
        except Exception as error:
            failures.append((name, f"{type(error).__name__}: {error}"))
            print(f"FAIL  {name}\n      {type(error).__name__}: {error}")
        else:
            print(f"PASS  {name}")
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
