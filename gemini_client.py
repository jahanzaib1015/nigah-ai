import base64
import io
import os
import threading
import time

import requests
from dotenv import load_dotenv
from google.ai import generativelanguage_v1beta as gl
from google.api_core import client_options as client_options_lib
from google.api_core import exceptions as core_exceptions
from PIL import Image

load_dotenv()

GEMINI_MODEL = "gemini-3.6-flash"
# claude-opus-4-8 was retired by the provider (verified live 2026-09-03: HTTP
# 503 model_not_found); claude-opus-5 is what TabiAI serves now and it reads
# note and medicine photos.
TABI_MODEL = "claude-opus-5"

# Key values live in .env and the Railway dashboard only - never in a committed
# file, because git history is permanent. GEMINI_API_KEY_1 also accepts the
# legacy GEMINI_API_KEY name so a deployment keeps its first tier working
# before the new variables are added in the dashboard, and TABI_AI_KEY accepts
# the older TABI_API_KEY spelling for the same reason.
_GOOGLE_KEY_1 = (os.getenv("GEMINI_API_KEY_1") or os.getenv("GEMINI_API_KEY") or "").strip()
_GOOGLE_KEY_2 = (os.getenv("GEMINI_API_KEY_2") or "").strip()
_TABI_KEY = (os.getenv("TABI_AI_KEY") or os.getenv("TABI_API_KEY") or "").strip()
_TABI_BASE_URL = (
    (os.getenv("TABI_BASE_URL") or "").strip().rstrip("/")
    or "https://tabitoken.com/v1"
)

# gunicorn kills the worker at --timeout 200 and the user gets a bare HTML 500
# with no Urdu audio, so the WHOLE chain - every tier, every retry, every sleep
# - has to finish inside a smaller budget and hand back a speakable error.
_CHAIN_BUDGET_SECONDS = 170
_PROVIDER_CAP_SECONDS = 100
_MIN_PROVIDER_SLICE_SECONDS = 20
_RETRY_PAUSE_SECONDS = 3
_MIN_ATTEMPT_SECONDS = 5
# One attempt is capped below the provider slice so a slow provider still gets a
# second try instead of spending its whole window on a single hung request.
_ATTEMPT_CAP_SECONDS = 60
_ABANDON_GRACE_SECONDS = 5
# The gRPC deadline is absolute, so this is a real wall-clock bound per Google
# attempt.
_REQUEST_TIMEOUT_SECONDS = 60
# A tier that has a successor gets ONE attempt and falls through on any error -
# the point of the chain is switching instantly on a 429, 5xx or timeout. The
# final tier keeps the old single-provider behaviour: retry once after a short
# pause when it is rate-limited, which is the only transient a same-key retry
# has ever fixed.
_MAX_ATTEMPTS = 2

# TabiAI sits behind a Cloudflare bot filter that rejects non-browser user
# agents, and it rejects request bodies above a size threshold, so that
# transport sends a browser UA and compressed images.
_TABI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_MAX_TABI_IMAGE_BYTES = 250_000
_TABI_RETRYABLE_STATUS = (408, 425, 429, 500, 502, 503, 504)


class VisionUnavailable(RuntimeError):
    """Every configured provider failed, so there is nothing left to parse."""


# Shared by both scan routes and spoken to the user whenever a scan cannot be
# answered - every provider failed, timed out, was rate-limited, or replied with
# something the validators cannot use. This app tells blind users which note or
# which medicine they are holding, so the only honest response to a failed scan
# is that it failed. Inventing a plausible answer is never acceptable.
SCAN_FAILED_ERROR = "Pehchan nahi ho saki. Dobara koshish karein."
SCAN_FAILED_VOICE = "پہچان نہیں ہو سکی۔ دوبارہ کوشش کریں۔"


def _build_tiers():
    """The real providers this process may call, in failover order."""
    tiers = []
    if _GOOGLE_KEY_1:
        tiers.append(("google-1", "google", _GOOGLE_KEY_1))
    if _GOOGLE_KEY_2:
        tiers.append(("google-2", "google", _GOOGLE_KEY_2))
    if _TABI_KEY:
        tiers.append(("tabi", "tabi", _TABI_KEY))
    return tiers


_TIERS = _build_tiers()

if not _TIERS:
    raise RuntimeError(
        "No vision provider is configured, so no scan could ever be answered. "
        "Set GEMINI_API_KEY_1, GEMINI_API_KEY_2 and TABI_AI_KEY in .env locally "
        "and under Variables in the Railway dashboard."
    )

_clients = {}


def _get_client(api_key):
    client = _clients.get(api_key)
    if client is None:
        client = gl.GenerativeServiceClient(
            client_options=client_options_lib.ClientOptions(api_key=api_key)
        )
        _clients[api_key] = client
    return client


def _shrink(image_data, mime_type):
    if len(image_data) <= _MAX_TABI_IMAGE_BYTES:
        return image_data, mime_type
    img = Image.open(io.BytesIO(image_data)).convert("RGB")
    data = b""
    # thumbnail() never enlarges, so each pass is a strictly smaller fallback
    # for images whose content (e.g. noise) resists JPEG compression.
    for size, quality in ((1024, 80), (768, 70), (512, 60), (384, 50)):
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= _MAX_TABI_IMAGE_BYTES:
            break
    return data, "image/jpeg"


def _run_bounded(func, args, timeout, label):
    """Run a provider transport and give up on it after `timeout` real seconds.

    A transport's own timeout is not a wall-clock bound: requests restarts its
    read timer on every byte received, so TabiAI once held a call open for 554s
    on a 100s timeout. That overran the chain budget and skipped the healthy
    provider entirely, so the bound has to come from outside the transport. The
    worker is a daemon, so an abandoned call can never keep the process alive.
    """
    outcome = {}

    def run():
        try:
            outcome["text"] = func(*args)
        except BaseException as error:  # re-raised in the calling thread
            outcome["error"] = error

    worker = threading.Thread(target=run, name=f"vision-{label}", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"{label} abandoned after {timeout:.0f}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["text"]


def _call_tabi(prompt, image_data, mime_type, timeout):
    content = [{"type": "text", "text": prompt}]
    if image_data is not None:
        image_data, mime_type = _shrink(image_data, mime_type)
        b64 = base64.b64encode(image_data).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}
        )

    deadline = time.monotonic() + timeout
    last_error = None
    attempt = 0
    while True:
        attempt += 1
        remaining = deadline - time.monotonic()
        # Starting an attempt that cannot finish inside its slice would push the
        # whole chain past gunicorn's worker timeout.
        if remaining < _MIN_ATTEMPT_SECONDS:
            break
        try:
            response = requests.post(
                f"{_TABI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {_TABI_KEY}",
                    "User-Agent": _TABI_UA,
                },
                json={
                    "model": TABI_MODEL,
                    "messages": [{"role": "user", "content": content}],
                },
                timeout=min(remaining, _ATTEMPT_CAP_SECONDS),
            )
        except requests.exceptions.RequestException as error:
            last_error = error
            print(f"[vision] tabi attempt {attempt} transport error: {error}", flush=True)
        else:
            if response.status_code in _TABI_RETRYABLE_STATUS:
                last_error = RuntimeError(f"TabiAI HTTP {response.status_code}")
                print(
                    f"[vision] tabi attempt {attempt} returned {response.status_code}",
                    flush=True,
                )
            else:
                # A Cloudflare block page or a reply missing `choices` is a
                # provider failure, not a caller bug - surface it so the chain
                # fails honestly instead of crashing the route.
                try:
                    response.raise_for_status()
                    payload = response.json()
                    text = payload["choices"][0]["message"]["content"]
                except Exception as error:
                    raise RuntimeError(
                        f"TabiAI unusable reply (HTTP {response.status_code}): {error}"
                    ) from error
                if not (text or "").strip():
                    raise RuntimeError("TabiAI returned an empty reply")
                return text
        if deadline - time.monotonic() <= _RETRY_PAUSE_SECONDS:
            break
        time.sleep(_RETRY_PAUSE_SECONDS)
    raise last_error or RuntimeError("TabiAI exhausted its time budget")


def _call_google(prompt, image_data, mime_type, timeout, api_key):
    parts = [gl.Part(text=prompt)]
    if image_data is not None:
        parts.append(gl.Part(inline_data=gl.Blob(mime_type=mime_type, data=image_data)))
    request = gl.GenerateContentRequest(
        model=f"models/{GEMINI_MODEL}",
        contents=[gl.Content(parts=parts)],
    )
    response = _get_client(api_key).generate_content(request, timeout=timeout)
    text = "".join(
        part.text
        for candidate in response.candidates
        for part in candidate.content.parts
    )
    if not text.strip():
        # Happens when a reply is safety-blocked: no candidates, no text.
        raise RuntimeError("Google Gemini returned an empty reply")
    return text


def generate(prompt, image_data=None, mime_type="image/jpeg", meta=None):
    """Run the provider chain and return whichever tier answers first.

    Order: Google key 1 -> Google key 2 -> TabiAI. A tier with a successor gets
    exactly one attempt, so a 429, 5xx, timeout or unusable reply falls through
    to the next tier instantly; the final tier retries once after a short pause
    only when it is rate-limited. When every tier fails the caller gets
    VisionUnavailable - the honest failure, never an invented answer. `meta` is
    an optional dict filled in with the winning provider and how long it took.
    """
    info = meta if isinstance(meta, dict) else None
    if info is not None:
        info.clear()
        info.update({"provider": None, "seconds": None})

    deadline = time.monotonic() + _CHAIN_BUDGET_SECONDS
    failures = []
    last_index = len(_TIERS) - 1
    for index, (name, kind, key) in enumerate(_TIERS):
        if deadline - time.monotonic() < _MIN_PROVIDER_SLICE_SECONDS:
            print(
                f"[vision] skipping {name}: chain budget nearly spent",
                flush=True,
            )
            break
        is_last = index == last_index
        attempts = _MAX_ATTEMPTS if is_last else 1
        for attempt in range(1, attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            started = time.monotonic()
            try:
                if kind == "tabi":
                    slice_seconds = min(remaining, _PROVIDER_CAP_SECONDS)
                    text = _run_bounded(
                        _call_tabi,
                        (prompt, image_data, mime_type, slice_seconds),
                        slice_seconds + _ABANDON_GRACE_SECONDS,
                        name,
                    )
                else:
                    text = _call_google(
                        prompt,
                        image_data,
                        mime_type,
                        min(_REQUEST_TIMEOUT_SECONDS, remaining),
                        key,
                    )
            except Exception as error:
                elapsed = time.monotonic() - started
                rate_limited = isinstance(error, core_exceptions.ResourceExhausted)
                print(
                    f"[vision] {name} FAILED in {elapsed:.1f}s"
                    f"{' (rate-limited 429)' if rate_limited else ''} "
                    f"({type(error).__name__}): {error}",
                    flush=True,
                )
                failures.append(f"{name}={type(error).__name__}")
                if is_last and rate_limited and attempt < attempts:
                    time.sleep(_RETRY_PAUSE_SECONDS)
                    continue
                break
            elapsed = time.monotonic() - started
            print(f"[vision] {name} answered in {elapsed:.1f}s", flush=True)
            if info is not None:
                info["provider"] = name
                info["seconds"] = round(elapsed, 1)
            return text

    detail = ", ".join(failures) or "none configured"
    print(f"[vision] chain exhausted ({detail}) - no provider answered", flush=True)
    raise VisionUnavailable(f"no vision provider succeeded ({detail})")


def health_summary():
    """One-line description of what this process will actually call."""
    names = "+".join(name for name, _, _ in _TIERS) or "none"
    models = []
    for _, kind, _ in _TIERS:
        model = GEMINI_MODEL if kind == "google" else TABI_MODEL
        if model not in models:
            models.append(model)
    return (
        f"providers={names}, models={','.join(models) or 'n/a'}, "
        "fallback=real-providers-only"
    )
