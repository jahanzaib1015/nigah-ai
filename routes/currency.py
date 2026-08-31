from flask import Blueprint, jsonify, request

import db
import gemini_client

CURRENCY_PROMPT = (
    "You are an expert on world currency notes. The image may show EITHER face "
    "of a note - read the denomination number and written words carefully. "
    "If the image is blurry, dark, empty, or no recognizable object is visible, "
    "reply with exactly: UNCLEAR. "
    "If the image is clear but does NOT show any currency note "
    "(for example a medicine, an object, a person), reply with exactly: NOT_CURRENCY. "
    "If it is a currency note, reply with ONLY two lines: "
    "line 1 the currency code - PKR (Pakistani Rupee), USD (US Dollar), "
    "GBP (British Pound), INR (Indian Rupee), EUR (Euro), or OTHER; "
    "line 2 the denomination number exactly as printed (for example 500, 100, 20). "
    "PKR back-side landmarks: 10 = Bab-ul-Khyber (Khyber Pass gate); "
    "20 = Mohenjo-daro ruins; 50 = Baltit Fort, Karimabad (Hunza); "
    "100 = Islamia College, Peshawar; 500 = Badshahi Mosque, Lahore; "
    "1000 = Faisal Mosque, Islamabad; 5000 = Iqbal Mausoleum, Lahore."
)

VALID_DENOMINATIONS = {"10", "20", "50", "100", "500", "1000", "5000"}
KNOWN_CURRENCIES = {"PKR", "USD", "GBP", "INR", "EUR", "OTHER"}

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
            jsonify(
                {
                    "success": False,
                    "error": "Koi photo nahi mili. Dobara koshish karein.",
                    "voice": "کوئی تصویر نہیں ملی۔ دوبارہ کوشش کریں۔",
                }
            ),
            400,
        )

    image_data = image.read()
    if not image_data:
        return jsonify(
            {
                "success": False,
                "error": "Scan kamyaab nahi hua. Dobara koshish karein.",
                "voice": "اسکین کامیاب نہیں ہوا۔ دوبارہ کوشش کریں۔",
            }
        )

    try:
        reply = gemini_client.generate(
            CURRENCY_PROMPT, image_data, image.mimetype or "image/jpeg"
        )
    except Exception as error:
        print(f"Gemini API error: {error}", flush=True)
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Scan kamyaab nahi hua. Dobara koshish karein.",
                    "voice": "اسکین کامیاب نہیں ہوا۔ دوبارہ کوشش کریں۔",
                }
            ),
            500,
        )

    lines = [line.strip() for line in reply.strip().splitlines() if line.strip()]

    if not lines or lines[0].upper() == "UNCLEAR":
        return jsonify(
            {
                "success": False,
                "error": "Currency note frame mein sahi tarah nazar nahi aa raha. "
                "Note ko camera ke samne seedha rakhein aur dobara koshish karein.",
                "voice": "کرنسی نوٹ فریم میں صحیح طریقے سے نظر نہیں آ رہا۔ "
                "نوٹ کو کیمرے کے سامنے سیدھا رکھیں اور دوبارہ کوشش کریں۔",
            }
        )

    if lines[0].upper() == "NOT_CURRENCY":
        return jsonify(
            {
                "success": False,
                "error": "Yeh currency note nahi hai. Sirf currency note ki tasveer lein.",
                "voice": "یہ کرنسی نوٹ نہیں ہے۔ صرف کرنسی نوٹ کی تصویر لیں۔",
            }
        )

    if len(lines) < 2:
        return jsonify(
            {
                "success": False,
                "error": "Scan kamyaab nahi hua. Dobara koshish karein.",
                "voice": "اسکین کامیاب نہیں ہوا۔ دوبارہ کوشش کریں۔",
            }
        )

    currency = lines[0].upper()
    denomination = lines[1]

    if currency not in KNOWN_CURRENCIES or not denomination.isdigit() or len(denomination) > 5:
        return jsonify(
            {
                "success": False,
                "error": "Scan kamyaab nahi hua. Dobara koshish karein.",
                "voice": "اسکین کامیاب نہیں ہوا۔ دوبارہ کوشش کریں۔",
            }
        )

    if currency == "PKR" and denomination not in VALID_DENOMINATIONS:
        return jsonify(
            {
                "success": False,
                "error": "Scan kamyaab nahi hua. Dobara koshish karein.",
                "voice": "اسکین کامیاب نہیں ہوا۔ دوبارہ کوشش کریں۔",
            }
        )

    name = f"Rs. {denomination}" if currency == "PKR" else f"{currency} {denomination}"
    db.add_item("currency", name, "success")
    return jsonify({"denomination": denomination, "currency": currency, "success": True})
