import asyncio
import hashlib
import os
import time

from flask import Blueprint, jsonify, request, send_file
import edge_tts

VOICE = "ur-PK-AsadNeural"
CACHE_TTL_SECONDS = 3600

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "speech_cache"
)
os.makedirs(CACHE_DIR, exist_ok=True)

speech_bp = Blueprint("speech", __name__)


@speech_bp.route("/generate-speech", methods=["POST"])
def generate_speech():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "No text provided"}), 400

    cache_path = os.path.join(
        CACHE_DIR, hashlib.md5(text.encode("utf-8")).hexdigest() + ".mp3"
    )
    cached = os.path.exists(cache_path) and (
        time.time() - os.path.getmtime(cache_path) < CACHE_TTL_SECONDS
    )

    if not cached:
        try:
            print(f"[speech] generating for text={text!r}", flush=True)
            asyncio.run(edge_tts.Communicate(text, VOICE).save(cache_path))
            print(f"[speech] generated {os.path.getsize(cache_path)} bytes", flush=True)
        except Exception as error:
            print(f"edge-tts error: {error}", flush=True)
            return jsonify({"success": False, "error": "Speech generation failed"}), 500
    else:
        print(
            f"[speech] cache hit for text={text!r} ({os.path.getsize(cache_path)} bytes)",
            flush=True,
        )

    return send_file(cache_path, mimetype="audio/mpeg")
