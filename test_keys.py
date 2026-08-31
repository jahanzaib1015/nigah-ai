import os
import sys

from dotenv import load_dotenv
from google.ai import generativelanguage_v1beta as gl
from google.api_core import client_options as client_options_lib

sys.stdout.reconfigure(errors="replace")
load_dotenv()

PRIMARY = (os.getenv("GEMINI_API_KEY_PRIMARY") or "").strip()
BACKUP = (os.getenv("GEMINI_API_KEY_BACKUP") or "").strip()


def probe(key, model, label):
    client = gl.GenerativeServiceClient(
        client_options=client_options_lib.ClientOptions(api_key=key)
    )
    request = gl.GenerateContentRequest(
        model=f"models/{model}",
        contents=[gl.Content(parts=[gl.Part(text="Hello")])],
    )
    try:
        response = client.generate_content(request)
        text = "".join(
            p.text for c in response.candidates for p in c.content.parts
        )
        print(f"[{label}] {model}: SUCCESS -> {text!r}")
        return True
    except Exception as e:
        code = getattr(e, "code", None) or type(e).__name__
        first = str(e).strip().splitlines()[0] if str(e).strip() else ""
        print(f"[{label}] {model}: FAILED code={code} :: {first}")
        return False


print("=== BACKUP key alone ===")
probe(BACKUP, "gemini-3.6-flash", "BACKUP")
probe(BACKUP, "gemini-2.5-flash", "BACKUP")
probe(BACKUP, "gemini-2.0-flash", "BACKUP")
probe(BACKUP, "gemini-1.5-flash", "BACKUP")

print("=== PRIMARY key alone ===")
probe(PRIMARY, "gemini-3.6-flash", "PRIMARY")
