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
    "Look at this image carefully. "
    "If the image is blurry, dark, empty, or no recognizable object is visible, "
    "reply with exactly: UNCLEAR. "
    "If the image is clear but does NOT show a medicine strip or package "
    "(for example a currency note, an object, a person), reply with exactly: NOT_MEDICINE. "
    "If it IS a medicine, reply with ONLY two lines: "
    "line 1 the brand name, "
    "line 2 the expiry date in YYYY-MM-DD format "
    "(or EXPIRY_NOT_VISIBLE if the expiry date is not visible or legible)."
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
        return (
            jsonify({"success": False, "error": "Koi photo nahi mili. Dobara koshish karein."}),
            400,
        )

    image_data = image.read()
    if not image_data:
        return jsonify({"success": False, "error": "Scan kamyaab nahi hua. Dobara koshish karein."})

    try:
        model = genai.GenerativeModel("gemini-3.6-flash")
        image_part = genai.protos.Part(
            inline_data={
                "mime_type": image.mimetype or "image/jpeg",
                "data": image_data,
            }
        )
        response = model.generate_content([MEDICINE_PROMPT, image_part])
        print(f"[medicine] gemini replied: {response.text!r}", flush=True)
    except Exception as error:
        print(f"Gemini API error: {error}", flush=True)
        return (
            jsonify({"success": False, "error": "Scan kamyaab nahi hua. Dobara koshish karein."}),
            500,
        )

    lines = [line.strip() for line in response.text.strip().splitlines() if line.strip()]

    if not lines or lines[0].upper() == "UNCLEAR":
        return jsonify(
            {
                "success": False,
                "error": "Dawai ki strip frame mein sahi tarah nazar nahi aa rahi. "
                "Dawai ko camera ke samne seedha rakhein aur dobara koshish karein.",
            }
        )

    if lines[0].upper() == "NOT_MEDICINE":
        return jsonify(
            {
                "success": False,
                "error": "Yeh dawai nahi hai. Sirf dawai ki strip ki tasveer lein.",
            }
        )

    name = lines[0]
    expiry_raw = lines[1] if len(lines) > 1 else "UNKNOWN"

    if not name or name.upper() == "UNKNOWN" or len(name) > 60:
        return jsonify({"success": False, "error": "Scan kamyaab nahi hua. Dobara koshish karein."})

    if expiry_raw.upper() in ("EXPIRY_NOT_VISIBLE", "UNKNOWN"):
        return jsonify(
            {
                "success": False,
                "error": "Dawai ka naam mil gaya lekin expiry date nazar nahi aa rahi. "
                "Expiry date wala hissa dikhayein.",
            }
        )

    try:
        expiry_date = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
    except ValueError:
        return jsonify(
            {
                "success": False,
                "error": "Dawai ka naam mil gaya lekin expiry date nazar nahi aa rahi. "
                "Expiry date wala hissa dikhayein.",
            }
        )

    status = "expired" if expiry_date and expiry_date < date.today() else "safe"
    db.add_item("medicine", name, status, expiry_date.isoformat() if expiry_date else None)

    return jsonify({"name": name, "status": status, "success": True})
