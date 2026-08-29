import os
from datetime import date, datetime

from dotenv import load_dotenv
from flask import Blueprint, jsonify, request
import google.generativeai as genai

import db

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing or empty in the .env file.")

genai.configure(api_key=api_key)

MEDICINE_PROMPT = (
    "This is a photo of a medicine package, strip or label. "
    "Identify the medicine's brand name and its expiry date. "
    "Reply with ONLY two lines: "
    "line 1 the brand name (or UNKNOWN), "
    "line 2 the expiry date in YYYY-MM-DD format (or UNKNOWN if not visible)."
)

medicine_bp = Blueprint("medicine", __name__)


@medicine_bp.route("/detect-medicine", methods=["GET", "POST"])
def detect_medicine():
    if request.method == "GET":
        return jsonify(
            {
                "success": False,
                "error": "Send a POST request with an image file in the 'image' field.",
            }
        )

    image = request.files.get("image")
    if image is None or image.filename == "":
        return jsonify({"success": False, "error": "No image file provided"}), 400

    image_data = image.read()
    if not image_data:
        return jsonify({"success": False, "error": "Could not identify"})

    try:
        model = genai.GenerativeModel("gemini-3.6-flash")
        image_part = genai.protos.Part(
            inline_data={
                "mime_type": image.mimetype or "image/jpeg",
                "data": image_data,
            }
        )
        response = model.generate_content([MEDICINE_PROMPT, image_part])
    except Exception as error:
        print(f"Gemini API error: {error}")
        return jsonify({"success": False, "error": "Could not identify"}), 500

    lines = [line.strip() for line in response.text.strip().splitlines() if line.strip()]
    name = lines[0] if lines else ""
    expiry_raw = lines[1] if len(lines) > 1 else "UNKNOWN"

    expiry_date = None
    try:
        expiry_date = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
    except ValueError:
        expiry_date = None

    if not name or name.upper() == "UNKNOWN" or len(name) > 60:
        return jsonify({"success": False, "error": "Could not identify"})

    status = "expired" if expiry_date and expiry_date < date.today() else "safe"
    db.add_item("medicine", name, status, expiry_date.isoformat() if expiry_date else None)

    return jsonify({"name": name, "status": status, "success": True})
