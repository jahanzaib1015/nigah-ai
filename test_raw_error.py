import os
import sys
import traceback

from dotenv import load_dotenv
import google.generativeai as genai

sys.stdout.reconfigure(errors="replace")

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print("GEMINI_API_KEY present:", bool(api_key))

genai.configure(api_key=api_key)

try:
    model = genai.GenerativeModel("gemini-3.6-flash")
    response = model.generate_content("Hello")
    print("SUCCESS")
    print(response.text)
except Exception as e:
    print("=== EXCEPTION TYPE ===")
    print(type(e))
    print("=== REPR ===")
    print(repr(e))
    print("=== STR ===")
    print(str(e))
    for attr in ("code", "status", "reason", "message", "details"):
        if hasattr(e, attr):
            print(f"=== e.{attr} ===")
            print(getattr(e, attr))
    resp = getattr(e, "response", None)
    if resp is not None:
        print("=== RAW HTTP STATUS ===")
        print(getattr(resp, "status_code", None))
        print("=== RAW HTTP BODY ===")
        print(getattr(resp, "text", None))
    print("=== FULL TRACEBACK ===")
    traceback.print_exc()
