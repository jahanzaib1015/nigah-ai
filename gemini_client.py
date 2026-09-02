import os
import time

from dotenv import load_dotenv
from google.ai import generativelanguage_v1beta as gl
from google.api_core import client_options as client_options_lib
from google.api_core import exceptions as core_exceptions

load_dotenv()

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = "gemini-3.6-flash"

# Google Gemini is the only vision provider. There is no second transport, no
# fallback chain and no synthetic reply: a scan either returns what the model
# actually read, or it raises and the route tells the user the scan failed.
# gunicorn kills the worker at --timeout 200 and a killed worker answers with a
# bare HTML 500 and no Urdu audio, so two attempts plus the pause between them
# stay well inside that. The gRPC deadline is absolute, so the per-attempt
# timeout is a real wall-clock bound.
_REQUEST_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2
_RETRY_PAUSE_SECONDS = 3

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set, so no scan could ever be answered. Set it in "
        ".env locally and under Variables in the Railway dashboard."
    )


class VisionUnavailable(RuntimeError):
    """Google Gemini did not return usable text, so there is nothing to parse."""


# Shared by both scan routes and spoken to the user whenever a scan cannot be
# answered - the provider failed, timed out, was rate-limited, or replied with
# something the validators cannot use. This app tells blind users which note or
# which medicine they are holding, so the only honest response to a failed scan
# is that it failed. Inventing a plausible answer is never acceptable.
SCAN_FAILED_ERROR = "Pehchan nahi ho saki. Dobara koshish karein."
SCAN_FAILED_VOICE = "پہچان نہیں ہو سکی۔ دوبارہ کوشش کریں۔"

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = gl.GenerativeServiceClient(
            client_options=client_options_lib.ClientOptions(api_key=GEMINI_API_KEY)
        )
    return _client


def _call_google(prompt, image_data, mime_type, timeout):
    parts = [gl.Part(text=prompt)]
    if image_data is not None:
        parts.append(gl.Part(inline_data=gl.Blob(mime_type=mime_type, data=image_data)))
    request = gl.GenerateContentRequest(
        model=f"models/{GEMINI_MODEL}",
        contents=[gl.Content(parts=parts)],
    )
    response = _get_client().generate_content(request, timeout=timeout)
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
    """Call Google Gemini and return the model's text.

    A 429 is retried once after a short pause, because the free tier reports
    transient rate limits that clear immediately. Every other failure raises
    VisionUnavailable straight away so the route can speak the honest failure.
    `meta` is an optional dict filled in with the provider and how long it took.
    """
    info = meta if isinstance(meta, dict) else None
    if info is not None:
        info.clear()
        info.update({"provider": None, "seconds": None})

    last_error = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            text = _call_google(prompt, image_data, mime_type, _REQUEST_TIMEOUT_SECONDS)
        except core_exceptions.ResourceExhausted as error:
            last_error = error
            print(
                f"[vision] google attempt {attempt} rate-limited (429) after "
                f"{time.monotonic() - started:.1f}s",
                flush=True,
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_PAUSE_SECONDS)
            continue
        except Exception as error:
            elapsed = time.monotonic() - started
            print(
                f"[vision] google FAILED in {elapsed:.1f}s "
                f"({type(error).__name__}): {error}",
                flush=True,
            )
            raise VisionUnavailable(
                f"google gemini failed ({type(error).__name__}): {error}"
            ) from error
        elapsed = time.monotonic() - started
        print(f"[vision] google answered in {elapsed:.1f}s", flush=True)
        if info is not None:
            info["provider"] = "google"
            info["seconds"] = round(elapsed, 1)
        return text

    raise VisionUnavailable(
        f"google gemini rate-limited on all {_MAX_ATTEMPTS} attempts: {last_error}"
    )


def health_summary():
    """One-line description of what this process will actually call."""
    return f"provider=google, model={GEMINI_MODEL}, fallback=none"
