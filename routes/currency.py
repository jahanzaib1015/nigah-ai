from flask import Blueprint, jsonify, request

import db
import gemini_client
from gemini_client import SERVICE_DOWN_ERROR, SERVICE_DOWN_VOICE

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

NO_PHOTO_ERROR = "Koi photo nahi mili. Dobara koshish karein."
NO_PHOTO_VOICE = "کوئی تصویر نہیں ملی۔ دوبارہ کوشش کریں۔"

UNCLEAR_ERROR = (
    "Currency note frame mein sahi tarah nazar nahi aa raha. "
    "Note ko camera ke samne seedha rakhein aur dobara koshish karein."
)
UNCLEAR_VOICE = (
    "کرنسی نوٹ فریم میں صحیح طریقے سے نظر نہیں آ رہا۔ "
    "نوٹ کو کیمرے کے سامنے سیدھا رکھیں اور دوبارہ کوشش کریں۔"
)

NOT_CURRENCY_ERROR = "Yeh currency note nahi hai. Sirf currency note ki tasveer lein."
NOT_CURRENCY_VOICE = "یہ کرنسی نوٹ نہیں ہے۔ صرف کرنسی نوٹ کی تصویر لیں۔"

# The vision call succeeded but the reply did not validate as a known note:
# distinct from SERVICE_DOWN_*, which means the call itself never returned.
UNRECOGNIZED_ERROR = (
    "Currency note ki pehchan nahi ho saki. Note ko saaf side camera ke "
    "samne rakhein aur dobara koshish karein."
)
UNRECOGNIZED_VOICE = (
    "کرنسی نوٹ کی پہچان نہیں ہو سکی۔ نوٹ کو صاف سائیڈ کیمرے کے سامنے "
    "رکھیں اور دوبارہ کوشش کریں۔"
)

currency_bp = Blueprint("currency", __name__)


def scan_failed(status=200, error=None, voice=None):
    return (
        jsonify(
            {
                "success": False,
                "error": error or "Scan kamyaab nahi hua. Dobara koshish karein.",
                "voice": voice or "اسکین کامیاب نہیں ہوا۔ دوبارہ کوشش کریں۔",
            }
        ),
        status,
    )


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
        return scan_failed(400, NO_PHOTO_ERROR, NO_PHOTO_VOICE)

    image_data = image.read()
    if not image_data:
        print("[currency] EMPTY UPLOAD: image field present but zero bytes", flush=True)
        return scan_failed(400, NO_PHOTO_ERROR, NO_PHOTO_VOICE)

    try:
        reply = gemini_client.generate(
            CURRENCY_PROMPT, image_data, image.mimetype or "image/jpeg"
        )
        print(f"[currency] gemini replied: {reply!r}", flush=True)
    except Exception as error:
        print(
            f"[currency] VISION API FAILURE ({type(error).__name__}): {error}",
            flush=True,
        )
        return scan_failed(503, SERVICE_DOWN_ERROR, SERVICE_DOWN_VOICE)

    lines = [line.strip() for line in reply.strip().splitlines() if line.strip()]

    if not lines or lines[0].upper() == "UNCLEAR":
        return scan_failed(200, UNCLEAR_ERROR, UNCLEAR_VOICE)

    if lines[0].upper() == "NOT_CURRENCY":
        return scan_failed(200, NOT_CURRENCY_ERROR, NOT_CURRENCY_VOICE)

    if len(lines) < 2:
        print(f"[currency] REJECTED: reply had no denomination line ({lines!r})", flush=True)
        return scan_failed(200, UNRECOGNIZED_ERROR, UNRECOGNIZED_VOICE)

    currency = lines[0].upper()
    denomination = lines[1]

    if currency not in KNOWN_CURRENCIES or not denomination.isdigit() or len(denomination) > 5:
        print(
            f"[currency] REJECTED: unparseable code/denomination "
            f"({currency!r}, {denomination!r})",
            flush=True,
        )
        return scan_failed(200, UNRECOGNIZED_ERROR, UNRECOGNIZED_VOICE)

    if currency == "PKR" and denomination not in VALID_DENOMINATIONS:
        print(
            f"[currency] REJECTED: {denomination!r} is not a valid PKR denomination",
            flush=True,
        )
        return scan_failed(200, UNRECOGNIZED_ERROR, UNRECOGNIZED_VOICE)

    name = f"Rs. {denomination}" if currency == "PKR" else f"{currency} {denomination}"
    db.add_item("currency", name, "success")
    return jsonify({"denomination": denomination, "currency": currency, "success": True})
