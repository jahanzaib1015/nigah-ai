import base64
import io
import os
import threading
import time
from datetime import date

from dotenv import load_dotenv
from google.ai import generativelanguage_v1beta as gl
from google.api_core import client_options as client_options_lib
from PIL import Image
import requests

load_dotenv()

TABI_API_KEY = (os.getenv("TABI_API_KEY") or "").strip()
TABI_BASE_URL = (os.getenv("TABI_BASE_URL") or "").strip().rstrip("/")
TABI_MODEL = "claude-opus-4-8"

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = "gemini-3.6-flash"

# gunicorn kills the worker at --timeout 200 and the user gets a bare HTML 500
# with no Urdu audio, so the WHOLE chain - every provider, every retry, every
# sleep - has to finish inside a smaller budget and hand back a speakable error.
_CHAIN_BUDGET_SECONDS = 170
_PROVIDER_CAP_SECONDS = 100
_MIN_PROVIDER_SLICE_SECONDS = 20
_RETRY_PAUSE_SECONDS = 3
_MIN_ATTEMPT_SECONDS = 5
# One attempt is capped below the provider slice so a slow provider still gets a
# second try instead of spending its whole window on a single hung request.
_ATTEMPT_CAP_SECONDS = 60
_ABANDON_GRACE_SECONDS = 5

# TabiAI sits behind a Cloudflare bot filter that rejects non-browser
# user agents, and it rejects request bodies above a size threshold,
# so that transport sends a browser UA and compressed images.
_TABI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_MAX_TABI_IMAGE_BYTES = 250_000
_TABI_RETRYABLE_STATUS = (408, 425, 429, 500, 502, 503, 504)

_TRUTHY = ("1", "true", "yes", "on")

# Synthetic replies are the LAST resort and OFF by default. This app tells blind
# users which medicine they are holding and which note they are paid in, so
# inventing an answer is worse than admitting the service is down. Set
# MOCK_VISION=1 only to exercise the frontend during an upstream outage; every
# mock response is flagged in the JSON and shouted about in the logs.
MOCK_VISION = (os.getenv("MOCK_VISION") or "").strip().lower() in _TRUTHY

# APP_MODE only decides which provider is tried FIRST - both stay wired up as
# long as their credentials exist, so an outage of the preferred one degrades to
# the other instead of failing the scan. railway.json cannot set environment
# variables, so this switch lives in the Railway dashboard under Variables.
_ON_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_SERVICE_ID"))


def _resolve_mode():
    explicit = (os.getenv("APP_MODE") or "").strip().lower()
    return explicit if explicit in ("production", "development") else "development"


APP_MODE = _resolve_mode()


class VisionUnavailable(RuntimeError):
    """Every configured provider failed, so there is nothing left to parse."""


def _available_providers():
    providers = []
    if TABI_API_KEY and TABI_BASE_URL:
        providers.append("tabi")
    if GEMINI_API_KEY:
        providers.append("google")
    if APP_MODE == "production":
        providers.reverse()
    return providers


PROVIDERS = _available_providers()
PROVIDER_MODELS = {"tabi": TABI_MODEL, "google": GEMINI_MODEL}

if not PROVIDERS and not MOCK_VISION:
    raise RuntimeError(
        "No vision provider is configured. Set TABI_API_KEY + TABI_BASE_URL "
        "(TabiAI) and/or GEMINI_API_KEY (Google Gemini) in .env, or set "
        "MOCK_VISION=1 to serve synthetic replies while testing the frontend."
    )

_google_client = None


def _get_google_client():
    # Built on first use so a deployment missing one provider's credentials can
    # still boot and serve from the other.
    global _google_client
    if _google_client is None:
        _google_client = gl.GenerativeServiceClient(
            client_options=client_options_lib.ClientOptions(api_key=GEMINI_API_KEY)
        )
    return _google_client


def _shrink(image_data, mime_type):
    if len(image_data) <= _MAX_TABI_IMAGE_BYTES:
        return image_data, mime_type
    img = Image.open(io.BytesIO(image_data)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue(), "image/jpeg"


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


def _generate_tabi(prompt, image_data, mime_type, timeout):
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
                f"{TABI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {TABI_API_KEY}",
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
                # moves on to Google instead of crashing the route.
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


def _generate_google(prompt, image_data, mime_type, timeout):
    parts = [gl.Part(text=prompt)]
    if image_data is not None:
        parts.append(gl.Part(inline_data=gl.Blob(mime_type=mime_type, data=image_data)))
    request = gl.GenerateContentRequest(
        model=f"models/{GEMINI_MODEL}",
        contents=[gl.Content(parts=parts)],
    )
    response = _get_google_client().generate_content(request, timeout=timeout)
    text = "".join(
        part.text
        for candidate in response.candidates
        for part in candidate.content.parts
    )
    if not text.strip():
        # Happens when a reply is safety-blocked: no candidates, no text.
        raise RuntimeError("Google Gemini returned an empty reply")
    return text


_PROVIDER_CALLS = {"tabi": _generate_tabi, "google": _generate_google}


def _shift_years(today, years):
    try:
        return today.replace(year=today.year + years)
    except ValueError:  # 29 February
        return today.replace(year=today.year + years, day=28)


def _mock_reply(task):
    today = date.today()
    if task == "currency":
        return "PKR\n500"
    if task == "label":
        return f"{_shift_years(today, 2).isoformat()}\n{_shift_years(today, -1).isoformat()}"
    if task == "medicine":
        return f"Panadol 500mg\n{_shift_years(today, 2).isoformat()}"
    return "MOCK_REPLY"


# Shared by both scan routes: shown when generate() itself fails (upstream
# timeout, 429/503, Cloudflare block, DeadlineExceeded), as opposed to a reply
# that parses badly.
SERVICE_DOWN_ERROR = (
    "Scanning service abhi available nahi hai. Thodi der baad koshish karein."
)
SERVICE_DOWN_VOICE = (
    "اسکیننگ سروس ابھی دستیاب نہیں ہے۔ تھوڑی دیر بعد کوشش کریں۔"
)


def generate(prompt, image_data=None, mime_type="image/jpeg", meta=None, task=None):
    """Run the provider chain and return the model's text.

    `meta` is an optional dict filled in with which provider answered, how long
    it took, and what each failed attempt reported - the routes log it and echo
    the provider back to the client. `task` only selects the mock reply.
    """
    info = meta if isinstance(meta, dict) else None
    if info is not None:
        info.clear()
        info.update({"provider": None, "mock": False, "attempts": []})

    deadline = time.monotonic() + _CHAIN_BUDGET_SECONDS
    for name in PROVIDERS:
        remaining = deadline - time.monotonic()
        if remaining < _MIN_PROVIDER_SLICE_SECONDS:
            print(
                f"[vision] skipping {name}: only {remaining:.0f}s left in the "
                f"{_CHAIN_BUDGET_SECONDS}s chain budget",
                flush=True,
            )
            break
        started = time.monotonic()
        slice_seconds = min(remaining, _PROVIDER_CAP_SECONDS)
        try:
            text = _run_bounded(
                _PROVIDER_CALLS[name],
                (prompt, image_data, mime_type, slice_seconds),
                slice_seconds + _ABANDON_GRACE_SECONDS,
                name,
            )
        except Exception as error:
            elapsed = time.monotonic() - started
            print(
                f"[vision] {name} FAILED in {elapsed:.1f}s "
                f"({type(error).__name__}): {error}",
                flush=True,
            )
            if info is not None:
                info["attempts"].append(
                    {
                        "provider": name,
                        "error": type(error).__name__,
                        "seconds": round(elapsed, 1),
                    }
                )
            continue
        elapsed = time.monotonic() - started
        print(f"[vision] {name} answered in {elapsed:.1f}s", flush=True)
        if info is not None:
            info["provider"] = name
            info["seconds"] = round(elapsed, 1)
        return text

    if MOCK_VISION:
        print(
            f"[vision] MOCK reply served (MOCK_VISION=1, task={task!r}) - "
            "this is NOT a real detection",
            flush=True,
        )
        if info is not None:
            info["provider"] = "mock"
            info["mock"] = True
        return _mock_reply(task)

    attempts = (info or {}).get("attempts", [])
    detail = ", ".join(f"{a['provider']}={a['error']}" for a in attempts)
    raise VisionUnavailable(
        f"no vision provider succeeded ({detail or 'none configured'})"
    )


def health_summary():
    """One-line description of what this process will actually call."""
    chain = "+".join(PROVIDERS) or "none"
    models = ",".join(PROVIDER_MODELS[name] for name in PROVIDERS) or "n/a"
    mock = "on" if MOCK_VISION else "off"
    return f"mode={APP_MODE}, providers={chain}, models={models}, mock={mock}"
