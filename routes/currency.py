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
    "This is a Pakistani currency note. Identify the exact denomination "
    "(10, 20, 50, 100, 500, 1000, or 5000 rupees). "
    "Reply with ONLY the number, nothing else."
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
        response = model.generate_content([CURRENCY_PROMPT, image_part])
        denomination = response.text.strip()
    except Exception as error:
        print(f"Gemini API error: {error}")
        return jsonify({"success": False, "error": "Could not identify"}), 500

    if denomination not in VALID_DENOMINATIONS:
        return jsonify({"success": False, "error": "Could not identify"})

    db.add_item("currency", f"{denomination} Rupees", "success")
    return jsonify({"denomination": denomination, "success": True})
