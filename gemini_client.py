import base64
import io
import os
import time

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

# Google's latency swings from a few seconds to several minutes. Give up just
# before gunicorn's --timeout 200 kills the worker, so the caller still returns
# the spoken service-unavailable message instead of a bare HTML 500.
_GOOGLE_TIMEOUT_SECONDS = 180

# railway.json cannot set environment variables, so APP_MODE is easy to leave
# out of the dashboard. Silently serving live traffic from the rate-limited dev
# proxy looks exactly like a detection bug, so a Railway runtime always means
# production unless Google is unusable there.
_ON_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_SERVICE_ID"))


def _resolve_mode():
    explicit = (os.getenv("APP_MODE") or "").strip().lower()
    if _ON_RAILWAY:
        if GEMINI_API_KEY:
            if explicit != "production":
                print(
                    "[gemini] Railway runtime detected: forcing production mode "
                    f"(APP_MODE={explicit or 'unset'})",
                    flush=True,
                )
            return "production"
        print(
            "[gemini] WARNING: Railway runtime without GEMINI_API_KEY - "
            "falling back to the development endpoint",
            flush=True,
        )
        return "development"
    return explicit if explicit in ("production", "development") else "development"


APP_MODE = _resolve_mode()

if APP_MODE == "production":
    if not GEMINI_API_KEY:
        raise RuntimeError("APP_MODE=production requires GEMINI_API_KEY in .env")
    _google_client = gl.GenerativeServiceClient(
        client_options=client_options_lib.ClientOptions(api_key=GEMINI_API_KEY)
    )
else:
    if not TABI_API_KEY or not TABI_BASE_URL:
        raise RuntimeError(
            "APP_MODE=development requires TABI_API_KEY and TABI_BASE_URL in .env"
        )

# TabiAI sits behind a Cloudflare bot filter that rejects non-browser
# user agents, and it rejects request bodies above a size threshold,
# so dev mode sends a browser UA and compressed images.
_TABI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_MAX_TABI_IMAGE_BYTES = 250_000


def _shrink(image_data, mime_type):
    if len(image_data) <= _MAX_TABI_IMAGE_BYTES:
        return image_data, mime_type
    img = Image.open(io.BytesIO(image_data)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue(), "image/jpeg"


def _generate_tabi(prompt, image_data, mime_type):
    content = [{"type": "text", "text": prompt}]
    if image_data is not None:
        image_data, mime_type = _shrink(image_data, mime_type)
        b64 = base64.b64encode(image_data).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}
        )
    # TabiAI is slow and rate-limited: allow one retry on transient errors.
    last_error = None
    for attempt in (0, 1):
        try:
            response = requests.post(
                f"{TABI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {TABI_API_KEY}", "User-Agent": _TABI_UA},
                json={"model": TABI_MODEL, "messages": [{"role": "user", "content": content}]},
                timeout=180,
            )
        except requests.exceptions.RequestException as error:
            last_error = error
            if attempt == 0:
                print(f"[gemini] TabiAI request failed ({error}), retrying once", flush=True)
                time.sleep(3)
                continue
            raise
        if response.status_code in (429, 503) and attempt == 0:
            last_error = RuntimeError(f"TabiAI HTTP {response.status_code}")
            print(f"[gemini] TabiAI returned {response.status_code}, retrying once", flush=True)
            time.sleep(5)
            continue
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    raise last_error


def _generate_google(prompt, image_data, mime_type):
    parts = [gl.Part(text=prompt)]
    if image_data is not None:
        parts.append(gl.Part(inline_data=gl.Blob(mime_type=mime_type, data=image_data)))
    request = gl.GenerateContentRequest(
        model=f"models/{GEMINI_MODEL}",
        contents=[gl.Content(parts=parts)],
    )
    response = _google_client.generate_content(request, timeout=_GOOGLE_TIMEOUT_SECONDS)
    return "".join(
        part.text
        for candidate in response.candidates
        for part in candidate.content.parts
    )


# Shared by both scan routes: shown when generate() itself fails (upstream
# timeout, 429/503, Cloudflare block), as opposed to a reply that parses badly.
SERVICE_DOWN_ERROR = (
    "Scanning service abhi available nahi hai. Thodi der baad koshish karein."
)
SERVICE_DOWN_VOICE = (
    "اسکیننگ سروس ابھی دستیاب نہیں ہے۔ تھوڑی دیر بعد کوشش کریں۔"
)


def generate(prompt, image_data=None, mime_type="image/jpeg"):
    if APP_MODE == "production":
        print(f"[gemini] production mode: Google Gemini endpoint ({GEMINI_MODEL})", flush=True)
        return _generate_google(prompt, image_data, mime_type)
    print(f"[gemini] development mode: TabiAI endpoint ({TABI_MODEL})", flush=True)
    return _generate_tabi(prompt, image_data, mime_type)
