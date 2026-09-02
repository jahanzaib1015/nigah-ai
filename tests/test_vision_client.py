"""Offline tests for the single-provider vision client in gemini_client.py.

Nothing here touches the network: the transport call and the clock are injected,
so the suite is free and instant (the Google free tier is 20 requests/day and
burns out if tests call it).
"""
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# gemini_client refuses to import without a key, which is the point of one of
# the checks below - the rest of the suite needs a deterministic one to load.
os.environ["GEMINI_API_KEY"] = "test-google-key"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import gemini_client
from google.api_core import exceptions as core_exceptions

# Everything gemini_client.py is allowed to define: one credential, one model,
# the two strings spoken when a scan cannot be answered, one exception, the two
# public functions, three tuning constants and the private transport helpers.
EXPECTED_SURFACE = {
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "SCAN_FAILED_ERROR",
    "SCAN_FAILED_VOICE",
    "VisionUnavailable",
    "generate",
    "health_summary",
    "_REQUEST_TIMEOUT_SECONDS",
    "_MAX_ATTEMPTS",
    "_RETRY_PAUSE_SECONDS",
    "_client",
    "_get_client",
    "_call_google",
}


class Clock:
    """Stand-in for time.monotonic/time.sleep so retry tests run instantly."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def swap(attr, value):
    """Replace one module attribute and return a callable that restores it."""
    saved = getattr(gemini_client, attr)
    setattr(gemini_client, attr, value)

    def restore():
        setattr(gemini_client, attr, saved)

    return restore


def answering(text, calls=None):
    """A transport that records each call and answers with `text`."""

    def call(prompt, image_data, mime_type, timeout):
        if calls is not None:
            calls.append((prompt, image_data, mime_type, timeout))
        return text

    return call


def failing(errors, calls=None):
    """A transport that raises errors[i] on call i, repeating the last one."""
    attempts = []

    def call(prompt, image_data, mime_type, timeout):
        index = len(attempts)
        attempts.append(timeout)
        if calls is not None:
            calls.append(timeout)
        raise errors[min(index, len(errors) - 1)]

    return call


def scripted(steps):
    """A transport driven by a list of ("raise", exc) / ("return", text) steps."""
    seen = []

    def call(prompt, image_data, mime_type, timeout):
        index = len(seen)
        seen.append(timeout)
        kind, value = steps[index]
        if kind == "raise":
            raise value
        return value

    return call, seen


def fake_client(candidate_texts):
    """A stand-in for the gRPC client: candidate_texts is a list of part lists."""
    calls = []

    class Client:
        def generate_content(self, request, timeout=None):
            calls.append((request, timeout))
            return types.SimpleNamespace(
                candidates=[
                    types.SimpleNamespace(
                        content=types.SimpleNamespace(
                            parts=[types.SimpleNamespace(text=part) for part in parts]
                        )
                    )
                    for parts in candidate_texts
                ]
            )

    return Client(), calls


CHECKS = []


def check(name):
    def decorate(fn):
        CHECKS.append((name, fn))
        return fn

    return decorate


@check("a real reply is returned exactly as the model wrote it")
def t_reply_is_passed_through():
    calls = []
    restore = swap("_call_google", answering("PKR\n1000", calls))
    try:
        meta = {"provider": "stale", "seconds": 99}
        text = gemini_client.generate("prompt", b"jpeg", "image/jpeg", meta=meta)
    finally:
        restore()
    assert text == "PKR\n1000", repr(text)
    assert calls == [("prompt", b"jpeg", "image/jpeg", gemini_client._REQUEST_TIMEOUT_SECONDS)], calls
    assert meta["provider"] == "google", meta
    assert isinstance(meta["seconds"], float), meta


@check("a 429 is retried once after a short pause")
def t_rate_limit_is_retried():
    call, seen = scripted(
        [
            ("raise", core_exceptions.ResourceExhausted("quota")),
            ("return", "PKR\n500"),
        ]
    )
    clock = Clock()
    restore = swap("_call_google", call)
    restore_time = swap("time", clock)
    try:
        meta = {}
        text = gemini_client.generate("prompt", None, "image/jpeg", meta=meta)
    finally:
        restore()
        restore_time()
    assert text == "PKR\n500", repr(text)
    assert seen == [gemini_client._REQUEST_TIMEOUT_SECONDS] * 2, seen
    assert clock.slept == [gemini_client._RETRY_PAUSE_SECONDS], clock.slept
    assert meta["provider"] == "google", meta


@check("rate limiting on every attempt is an honest failure")
def t_rate_limit_exhausts():
    call, seen = scripted(
        [
            ("raise", core_exceptions.ResourceExhausted("quota")),
            ("raise", core_exceptions.ResourceExhausted("quota")),
        ]
    )
    clock = Clock()
    restore = swap("_call_google", call)
    restore_time = swap("time", clock)
    try:
        gemini_client.generate("prompt", None, "image/jpeg")
    except gemini_client.VisionUnavailable as error:
        message = str(error)
    else:
        raise AssertionError("two 429s must not produce a reply")
    finally:
        restore()
        restore_time()
    assert len(seen) == gemini_client._MAX_ATTEMPTS, seen
    assert "rate-limited" in message, message


@check("any other error fails at once with no retry")
def t_other_errors_are_not_retried():
    for error in (
        RuntimeError("transport is down"),
        core_exceptions.DeadlineExceeded("timed out"),
        core_exceptions.PermissionDenied("bad key"),
        ValueError("malformed reply"),
    ):
        seen = []
        restore = swap("_call_google", failing([error], seen))
        try:
            gemini_client.generate("prompt", None, "image/jpeg")
        except gemini_client.VisionUnavailable as raised:
            message = str(raised)
        else:
            raise AssertionError(f"{type(error).__name__} must not produce a reply")
        finally:
            restore()
        assert len(seen) == 1, (type(error).__name__, seen)
        assert type(error).__name__ in message, message


@check("a safety-blocked reply with no text is a failure, not a blank result")
def t_empty_reply_fails():
    client, calls = fake_client([[], [""]])
    restore = swap("_get_client", lambda: client)
    try:
        gemini_client._call_google("prompt", None, "image/jpeg", 60)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("an empty reply must not be returned as text")
    finally:
        restore()
    assert "empty reply" in message, message
    # The gRPC deadline is what actually bounds a hung request, so it has to be
    # the caller's timeout and not something the client picks on its own.
    assert [timeout for _, timeout in calls] == [60], calls


@check("multi-part replies are joined in order")
def t_parts_are_joined():
    client, calls = fake_client([["PKR\n", "1000"]])
    restore = swap("_get_client", lambda: client)
    try:
        text = gemini_client._call_google("prompt", b"jpeg", "image/jpeg", 60)
    finally:
        restore()
    assert text == "PKR\n1000", repr(text)
    request = calls[0][0]
    assert request.model == f"models/{gemini_client.GEMINI_MODEL}", request.model
    assert len(request.contents[0].parts) == 2, request.contents[0].parts


@check("an image is sent inline, never uploaded")
def t_image_is_inline():
    client, calls = fake_client([["PKR\n1000"]])
    restore = swap("_get_client", lambda: client)
    try:
        gemini_client._call_google("prompt", b"\xff\xd8jpeg", "image/jpeg", 60)
    finally:
        restore()
    parts = calls[0][0].contents[0].parts
    assert parts[0].text == "prompt", parts[0]
    blob = parts[1].inline_data
    assert blob.mime_type == "image/jpeg", blob
    assert blob.data == b"\xff\xd8jpeg", blob


@check("/health names the one provider, the model and no fallback")
def t_health_summary():
    summary = gemini_client.health_summary()
    assert summary == "provider=google, model=gemini-3.6-flash, fallback=none", summary


@check("the failure message is real Urdu and says the scan failed")
def t_failure_message():
    assert gemini_client.SCAN_FAILED_ERROR.strip(), gemini_client.SCAN_FAILED_ERROR
    voice = gemini_client.SCAN_FAILED_VOICE
    assert voice.strip() and voice != gemini_client.SCAN_FAILED_ERROR, voice
    # Screen readers get Urdu script; the on-screen line is Roman Urdu.
    assert any("\u0600" <= char <= "\u06ff" for char in voice), voice
    assert all(char.isascii() for char in gemini_client.SCAN_FAILED_ERROR), voice


@check("the client has no way to answer except by calling Google")
def t_client_surface():
    """Pinned to an allowlist rather than a denylist. A denylist only catches
    the retired names someone already thought of; this fails the moment ANY new
    attribute appears - a second provider, a budget loop, a canned reply - which
    is the real invariant: exactly one transport, and it is Google's."""
    imported = {
        "os",
        "time",
        "load_dotenv",
        "gl",
        "client_options_lib",
        "core_exceptions",
    }
    names = {
        name
        for name in vars(gemini_client)
        if not name.startswith("__") and name not in imported
    }
    extra = names - EXPECTED_SURFACE
    assert not extra, f"unexpected client surface: {sorted(extra)}"
    missing = EXPECTED_SURFACE - names
    assert not missing, f"client lost: {sorted(missing)}"
    assert gemini_client.GEMINI_MODEL == "gemini-3.6-flash", gemini_client.GEMINI_MODEL
    assert gemini_client.GEMINI_API_KEY == "test-google-key", gemini_client.GEMINI_API_KEY
    assert gemini_client._MAX_ATTEMPTS == 2, gemini_client._MAX_ATTEMPTS
    assert gemini_client._REQUEST_TIMEOUT_SECONDS * gemini_client._MAX_ATTEMPTS + \
        gemini_client._RETRY_PAUSE_SECONDS < 200, "would outlive gunicorn's worker timeout"


@check("the process refuses to start without GEMINI_API_KEY")
def t_keyless_import_fails():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "try:\n"
        "    import gemini_client\n"
        "except RuntimeError as error:\n"
        "    print('GUARD:', error)\n"
        "else:\n"
        "    print('IMPORTED')\n"
    )
    env = {key: value for key, value in os.environ.items() if key != "GEMINI_API_KEY"}
    # A temp cwd keeps load_dotenv() from picking up the developer's real .env.
    with tempfile.TemporaryDirectory() as empty:
        probe = subprocess.run(
            [sys.executable, "-c", script],
            cwd=empty,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.startswith("GUARD:"), probe.stdout
    assert "GEMINI_API_KEY" in probe.stdout, probe.stdout


def main():
    failures = []
    for name, fn in CHECKS:
        try:
            fn()
        except Exception as error:
            failures.append(name)
            print(f"FAIL  {name}\n      {type(error).__name__}: {error}")
        else:
            print(f"PASS  {name}")
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
