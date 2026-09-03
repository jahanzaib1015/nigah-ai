"""Offline tests for the three-tier vision chain in gemini_client.py.

Nothing here touches the network: the transports and the clock are injected,
so the suite is free and instant (the Google free tier is 20 requests/day per
key and burns out if tests call it).
"""
import io
import os
import subprocess
import sys
import tempfile
import time as real_time
import types
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# gemini_client refuses to import without any provider key, which is the point
# of one of the checks below - the rest of the suite needs deterministic fake
# credentials to load. They are set BEFORE import so the developer's real .env
# cannot leak real keys into the test process (load_dotenv never replaces
# existing variables) and no check can ever reach a real provider.
os.environ["GEMINI_API_KEY_1"] = "test-google-key-1"
os.environ["GEMINI_API_KEY_2"] = "test-google-key-2"
os.environ["TABI_AI_KEY"] = "test-tabi-key"
os.environ["TABI_BASE_URL"] = "https://tabi.invalid/v1"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import gemini_client
from google.api_core import exceptions as core_exceptions

# Everything gemini_client.py is allowed to define: the three tier credentials,
# the two models, the two strings spoken when a scan cannot be answered, one
# exception, the two public functions, the chain's tuning constants and the
# private transport helpers. Nothing else may appear - no fourth provider, no
# budget loop, and above all no canned reply.
imported = {
    "base64",
    "io",
    "os",
    "threading",
    "time",
    "requests",
    "load_dotenv",
    "gl",
    "client_options_lib",
    "core_exceptions",
    "Image",
}
EXPECTED_SURFACE = {
    "GEMINI_MODEL",
    "TABI_MODEL",
    "SCAN_FAILED_ERROR",
    "SCAN_FAILED_VOICE",
    "VisionUnavailable",
    "generate",
    "health_summary",
    "_GOOGLE_KEY_1",
    "_GOOGLE_KEY_2",
    "_TABI_KEY",
    "_TABI_BASE_URL",
    "_TIERS",
    "_build_tiers",
    "_clients",
    "_get_client",
    "_call_google",
    "_call_tabi",
    "_shrink",
    "_run_bounded",
    "_TABI_UA",
    "_MAX_TABI_IMAGE_BYTES",
    "_TABI_RETRYABLE_STATUS",
    "_REQUEST_TIMEOUT_SECONDS",
    "_MAX_ATTEMPTS",
    "_RETRY_PAUSE_SECONDS",
    "_CHAIN_BUDGET_SECONDS",
    "_PROVIDER_CAP_SECONDS",
    "_MIN_PROVIDER_SLICE_SECONDS",
    "_MIN_ATTEMPT_SECONDS",
    "_ATTEMPT_CAP_SECONDS",
    "_ABANDON_GRACE_SECONDS",
}

GOOGLE_1 = ("google-1", "google", "test-google-key-1")
GOOGLE_2 = ("google-2", "google", "test-google-key-2")
TABI = ("tabi", "tabi", "test-tabi-key")


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
    """A Google transport that records each call and answers with `text`."""

    def call(prompt, image_data, mime_type, timeout, api_key=None):
        if calls is not None:
            calls.append((prompt, image_data, mime_type, timeout, api_key))
        return text

    return call


def scripted(steps, calls=None):
    """A Google transport driven by ("raise", exc) / ("return", text) steps,
    repeating the last one. Records the (api_key, timeout) of every attempt."""

    seen = []

    def call(prompt, image_data, mime_type, timeout, api_key=None):
        index = len(seen)
        seen.append((api_key, timeout))
        if calls is not None:
            calls.append(timeout)
        kind, value = steps[min(index, len(steps) - 1)]
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


def tabi_response(status=200, content="PKR\n1000"):
    def raise_for_status():
        if status >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {status}")

    return types.SimpleNamespace(
        status_code=status,
        text='{"stub": true}',
        json=lambda: {"choices": [{"message": {"content": content}}]},
        raise_for_status=raise_for_status,
    )


def fake_requests(post):
    """A requests stand-in that keeps the real exceptions module for the
    transport's except clauses."""
    return types.SimpleNamespace(post=post, exceptions=requests.exceptions)


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
    assert calls == [
        ("prompt", b"jpeg", "image/jpeg", gemini_client._REQUEST_TIMEOUT_SECONDS,
         "test-google-key-1")
    ], calls
    assert meta["provider"] == "google-1", meta
    assert isinstance(meta["seconds"], float), meta


@check("a 429 on the first key falls through to the second key instantly")
def t_429_falls_through_instantly():
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
    assert [key for key, _ in seen] == ["test-google-key-1", "test-google-key-2"], seen
    # Instant switch: no pause is ever slept between tiers.
    assert clock.slept == [], clock.slept
    assert meta["provider"] == "google-2", meta


@check("timeouts cascade through every tier until one answers")
def t_timeout_cascades_to_tabi():
    google, gseen = scripted(
        [
            ("raise", core_exceptions.DeadlineExceeded("timed out")),
            ("raise", core_exceptions.ResourceExhausted("quota")),
        ]
    )
    tcalls = []

    def tabi(prompt, image_data, mime_type, timeout):
        tcalls.append(timeout)
        return "PKR\n100"

    restore_g = swap("_call_google", google)
    restore_t = swap("_call_tabi", tabi)
    try:
        meta = {}
        text = gemini_client.generate("prompt", b"jpeg", "image/jpeg", meta=meta)
    finally:
        restore_g()
        restore_t()
    assert text == "PKR\n100", repr(text)
    assert [key for key, _ in gseen] == ["test-google-key-1", "test-google-key-2"], gseen
    assert len(tcalls) == 1, tcalls
    assert meta["provider"] == "tabi", meta


@check("every tier failing is the honest failure, with each tier named")
def t_chain_exhaustion_is_honest():
    google, gseen = scripted(
        [("raise", core_exceptions.DeadlineExceeded("timed out"))]
    )
    tcalls = []

    def tabi(prompt, image_data, mime_type, timeout):
        tcalls.append(timeout)
        raise RuntimeError("TabiAI HTTP 503")

    restore_g = swap("_call_google", google)
    restore_t = swap("_call_tabi", tabi)
    try:
        gemini_client.generate("prompt", None, "image/jpeg")
    except gemini_client.VisionUnavailable as error:
        message = str(error)
    else:
        raise AssertionError("a fully failed chain must not produce a reply")
    finally:
        restore_g()
        restore_t()
    assert [key for key, _ in gseen] == ["test-google-key-1", "test-google-key-2"], gseen
    # TabiAI is the last tier, but its error is not a 429, so no pointless retry.
    assert len(tcalls) == 1, tcalls
    for tier in ("google-1", "google-2", "tabi"):
        assert tier in message, message


@check("with a single tier, a 429 is still retried once after a short pause")
def t_single_tier_429_retry():
    call, seen = scripted(
        [
            ("raise", core_exceptions.ResourceExhausted("quota")),
            ("return", "PKR\n500"),
        ]
    )
    clock = Clock()
    restore_tiers = swap("_TIERS", [GOOGLE_1])
    restore = swap("_call_google", call)
    restore_time = swap("time", clock)
    try:
        meta = {}
        text = gemini_client.generate("prompt", None, "image/jpeg", meta=meta)
    finally:
        restore()
        restore_time()
        restore_tiers()
    assert text == "PKR\n500", repr(text)
    assert [key for key, _ in seen] == ["test-google-key-1", "test-google-key-1"], seen
    assert clock.slept == [gemini_client._RETRY_PAUSE_SECONDS], clock.slept
    assert meta["provider"] == "google-1", meta


@check("with a single tier, 429 on every attempt is an honest failure")
def t_single_tier_429_exhausts():
    call, seen = scripted(
        [("raise", core_exceptions.ResourceExhausted("quota"))]
    )
    clock = Clock()
    restore_tiers = swap("_TIERS", [GOOGLE_1])
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
        restore_tiers()
    assert len(seen) == gemini_client._MAX_ATTEMPTS, seen
    assert "ResourceExhausted" in message, message
    assert "google-1" in message, message


@check("with a single tier, any other error fails at once with no retry")
def t_single_tier_other_errors():
    for error in (
        RuntimeError("transport is down"),
        core_exceptions.DeadlineExceeded("timed out"),
        core_exceptions.PermissionDenied("bad key"),
        ValueError("malformed reply"),
    ):
        seen = []

        def call(prompt, image_data, mime_type, timeout, api_key=None, raised=error):
            seen.append(api_key)
            raise raised

        restore_tiers = swap("_TIERS", [GOOGLE_1])
        restore = swap("_call_google", call)
        try:
            gemini_client.generate("prompt", None, "image/jpeg")
        except gemini_client.VisionUnavailable as raised:
            message = str(raised)
        else:
            raise AssertionError(f"{type(error).__name__} must not produce a reply")
        finally:
            restore()
            restore_tiers()
        assert len(seen) == 1, (type(error).__name__, seen)
        assert type(error).__name__ in message, message


@check("a safety-blocked reply with no text is a failure, not a blank result")
def t_empty_reply_fails():
    client, calls = fake_client([[]])
    restore = swap("_get_client", lambda api_key: client)
    try:
        gemini_client._call_google("prompt", None, "image/jpeg", 60, "k")
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
    restore = swap("_get_client", lambda api_key: client)
    try:
        text = gemini_client._call_google("prompt", b"jpeg", "image/jpeg", 60, "k")
    finally:
        restore()
    assert text == "PKR\n1000", repr(text)
    request = calls[0][0]
    assert request.model == f"models/{gemini_client.GEMINI_MODEL}", request.model
    assert len(request.contents[0].parts) == 2, request.contents[0].parts


@check("an image is sent inline, never uploaded")
def t_image_is_inline():
    client, calls = fake_client([["PKR\n1000"]])
    restore = swap("_get_client", lambda api_key: client)
    try:
        gemini_client._call_google("prompt", b"\xff\xd8jpeg", "image/jpeg", 60, "k")
    finally:
        restore()
    parts = calls[0][0].contents[0].parts
    assert parts[0].text == "prompt", parts[0]
    blob = parts[1].inline_data
    assert blob.mime_type == "image/jpeg", blob
    assert blob.data == b"\xff\xd8jpeg", blob


@check("each Google key gets its own cached client")
def t_clients_are_per_key():
    made = []

    def constructor(client_options=None):
        made.append(client_options.api_key)
        return types.SimpleNamespace(api_key=client_options.api_key)

    restore_gl = swap("gl", types.SimpleNamespace(GenerativeServiceClient=constructor))
    try:
        first = gemini_client._get_client("key-a")
        again = gemini_client._get_client("key-a")
        second = gemini_client._get_client("key-b")
    finally:
        restore_gl()
        gemini_client._clients.clear()
    assert first is again
    assert first is not second
    assert made == ["key-a", "key-b"], made


@check("the TabiAI call sends the browser UA, bearer key and a data-URI image")
def t_tabi_payload():
    posts = []

    def post(url, headers=None, json=None, timeout=None):
        posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return tabi_response()

    restore = swap("requests", fake_requests(post))
    try:
        text = gemini_client._call_tabi("prompt", b"\xff\xd8jpeg", "image/jpeg", 50)
    finally:
        restore()
    assert text == "PKR\n1000", repr(text)
    assert len(posts) == 1, posts
    sent = posts[0]
    assert sent["url"] == "https://tabi.invalid/v1/chat/completions", sent["url"]
    assert sent["headers"]["Authorization"] == "Bearer test-tabi-key", sent["headers"]
    assert sent["headers"]["User-Agent"] == gemini_client._TABI_UA, sent["headers"]
    body = sent["json"]
    assert body["model"] == "claude-opus-5", body
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "prompt"}, content
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"), content


@check("TabiAI retries a retryable status and succeeds on the next round")
def t_tabi_retries_retryable_status():
    posts = []

    def post(url, headers=None, json=None, timeout=None):
        posts.append(None)
        if len(posts) == 1:
            return tabi_response(status=503)
        return tabi_response(content="PKR\n20")

    clock = Clock()
    restore = swap("requests", fake_requests(post))
    restore_time = swap("time", clock)
    try:
        text = gemini_client._call_tabi("prompt", None, "image/jpeg", 50)
    finally:
        restore()
        restore_time()
    assert text == "PKR\n20", repr(text)
    assert len(posts) == 2, posts
    assert clock.slept == [gemini_client._RETRY_PAUSE_SECONDS], clock.slept


@check("a non-retryable TabiAI failure surfaces immediately")
def t_tabi_non_retryable():
    posts = []

    def post(url, headers=None, json=None, timeout=None):
        posts.append(None)
        return tabi_response(status=401)

    restore = swap("requests", fake_requests(post))
    try:
        gemini_client._call_tabi("prompt", None, "image/jpeg", 50)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("an unauthorized TabiAI must not produce a reply")
    finally:
        restore()
    assert len(posts) == 1, posts
    assert "unusable reply" in message, message
    assert "401" in message, message


@check("an empty TabiAI reply is a failure, not a blank result")
def t_tabi_empty_reply():
    def post(url, headers=None, json=None, timeout=None):
        return tabi_response(content="   ")

    restore = swap("requests", fake_requests(post))
    try:
        gemini_client._call_tabi("prompt", None, "image/jpeg", 50)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("an empty reply must not be returned as text")
    finally:
        restore()
    assert "empty reply" in message, message


@check("oversized images are shrunk under TabiAI's limit, small inputs pass through")
def t_shrink():
    small = b"\xff\xd8tinyjpeg"
    assert gemini_client._shrink(small, "image/jpeg") == (small, "image/jpeg")

    import random

    random.seed(7)
    raw = random.randbytes(2400 * 2400 * 3)
    noisy = Image.frombytes("RGB", (2400, 2400), raw)
    buffer = io.BytesIO()
    noisy.save(buffer, format="JPEG", quality=95)
    photo = buffer.getvalue()
    assert len(photo) > gemini_client._MAX_TABI_IMAGE_BYTES, len(photo)
    data, mime = gemini_client._shrink(photo, "image/jpeg")
    assert len(data) <= gemini_client._MAX_TABI_IMAGE_BYTES, len(data)
    assert mime == "image/jpeg", mime
    assert Image.open(io.BytesIO(data)).size <= (1024, 1024)


@check("a transport that hangs is abandoned at the wall-clock bound")
def t_run_bounded():
    def slow(*args):
        real_time.sleep(0.5)
        return "late"

    started = real_time.monotonic()
    try:
        gemini_client._run_bounded(slow, (), 0.1, "slow")
    except TimeoutError as error:
        message = str(error)
    else:
        raise AssertionError("a hung transport must not be waited on")
    elapsed = real_time.monotonic() - started
    assert elapsed < 0.4, elapsed
    assert "abandoned" in message, message

    def boom(*args):
        raise ValueError("boom")

    try:
        gemini_client._run_bounded(boom, (), 5, "boom")
    except ValueError as error:
        assert "boom" in str(error)
    else:
        raise AssertionError("a transport error must propagate, not vanish")

    assert gemini_client._run_bounded(lambda: "ok", (), 5, "ok") == "ok"


@check("/health names every tier, both models and no synthetic fallback")
def t_health_summary():
    summary = gemini_client.health_summary()
    assert summary == (
        "providers=google-1+google-2+tabi, "
        "models=gemini-3.6-flash,claude-opus-5, "
        "fallback=real-providers-only"
    ), summary


@check("the failure message is real Urdu and says the scan failed")
def t_failure_message():
    assert gemini_client.SCAN_FAILED_ERROR.strip(), gemini_client.SCAN_FAILED_ERROR
    voice = gemini_client.SCAN_FAILED_VOICE
    assert voice.strip() and voice != gemini_client.SCAN_FAILED_ERROR, voice
    # Screen readers get Urdu script; the on-screen line is Roman Urdu.
    assert any("\u0600" <= char <= "\u06ff" for char in voice), voice
    assert all(char.isascii() for char in gemini_client.SCAN_FAILED_ERROR), voice


@check("the client can only answer through the three real transports")
def t_client_surface():
    """Pinned to an allowlist rather than a denylist. A denylist only catches
    the retired names someone already thought of; this fails the moment ANY new
    attribute appears - a fourth provider, a budget loop, a canned reply. The
    source check below keeps every spelling of a synthetic reply out."""
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
    assert gemini_client.TABI_MODEL == "claude-opus-5", gemini_client.TABI_MODEL
    assert gemini_client._GOOGLE_KEY_1 == "test-google-key-1", gemini_client._GOOGLE_KEY_1
    assert gemini_client._GOOGLE_KEY_2 == "test-google-key-2", gemini_client._GOOGLE_KEY_2
    assert gemini_client._TABI_KEY == "test-tabi-key", gemini_client._TABI_KEY
    assert gemini_client._MAX_ATTEMPTS == 2, gemini_client._MAX_ATTEMPTS
    assert gemini_client._CHAIN_BUDGET_SECONDS < 200, \
        "the chain would outlive gunicorn's worker timeout"
    kinds = {kind for _, kind, _ in gemini_client._TIERS}
    assert kinds <= {"google", "tabi"}, kinds
    source = Path(gemini_client.__file__).read_text(encoding="utf-8").lower()
    assert "mock" not in source, "no synthetic reply mechanism may exist in the client"


@check("the process refuses to start without any provider key")
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
    strip = (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "TABI_API_KEY",
        "TABI_AI_KEY",
    )
    env = {key: value for key, value in os.environ.items() if key not in strip}
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
    assert "GEMINI_API_KEY_1" in probe.stdout, probe.stdout


@check("the legacy variable names keep older deployments on the chain")
def t_legacy_env_names_still_work():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import gemini_client\n"
        "print(gemini_client._GOOGLE_KEY_1, gemini_client._TABI_KEY)\n"
        "print(gemini_client.health_summary())\n"
    )
    strip = ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "TABI_API_KEY", "TABI_AI_KEY")
    env = {key: value for key, value in os.environ.items() if key not in strip}
    env["GEMINI_API_KEY"] = "legacy-google-key"
    env["TABI_API_KEY"] = "legacy-tabi-key"
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
    lines = probe.stdout.strip().splitlines()
    assert lines and lines[0] == "legacy-google-key legacy-tabi-key", lines
    assert "providers=google-1+tabi" in lines[1], lines


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
