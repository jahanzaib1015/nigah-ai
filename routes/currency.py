import os

from dotenv import load_dotenv
from flask import Blueprint, jsonify, request
import google.generativeai as genai

import db

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing or empty in the .env file.")

genai.configure(api_key=api_key)

CURRENCY_PROMPT = (
    "Look at this image carefully. "
    "If the image is blurry, dark, empty, or no recognizable object is visible, "
    "reply with exactly: UNCLEAR. "
    "If the image is clear but does NOT show a Pakistani currency note "
    "(for example a medicine, an object, a person), reply with exactly: NOT_CURRENCY. "
    "If it is a Pakistani currency note, reply with ONLY the denomination number "
    "(10, 20, 50, 100, 500, 1000, or 5000)."
)

VALID_DENOMINATIONS = {"10", "20", "50", "100", "500", "1000", "5000"}

currency_bp = Blueprint("currency", __name__)


@currency_bp.route("/detect-currency", methods=["GET", "POST"])
def detect_currency():
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
        response = model.generate_content([CURRENCY_PROMPT, image_part])
        denomination = response.text.strip()
    except Exception as error:
        print(f"Gemini API error: {error}")
        return (
            jsonify({"success": False, "error": "Scan kamyaab nahi hua. Dobara koshish karein."}),
            500,
        )

    if denomination == "UNCLEAR":
        return jsonify(
            {
                "success": False,
                "error": "Currency note frame mein sahi tarah nazar nahi aa raha. "
                "Note ko camera ke samne seedha rakhein aur dobara koshish karein.",
            }
        )

    if denomination == "NOT_CURRENCY":
        return jsonify(
            {
                "success": False,
                "error": "Yeh currency note nahi hai. Sirf currency note ki tasveer lein.",
            }
        )

    if denomination not in VALID_DENOMINATIONS:
        return jsonify({"success": False, "error": "Scan kamyaab nahi hua. Dobara koshish karein."})

    db.add_item("currency", f"Rs. {denomination}", "success")
    return jsonify({"denomination": denomination, "success": True})
