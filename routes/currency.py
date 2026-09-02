import re

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
    "line 2 the denomination number exactly as printed (for example 500, 100, 20), "
    "with no currency symbol, no word 'rupees' and no other text on that line. "
    "PKR notes are only issued in these denominations: 5, 10, 20, 50, 75, 100, "
    "200, 500, 1000, 5000 - if the note is Pakistani, line 2 MUST be exactly one "
    "of those numbers, so pick the closest one you can actually read on the note. "
    "PKR back-side landmarks: 10 = Bab-ul-Khyber (Khyber Pass gate); "
    "20 = Mohenjo-daro ruins; 50 = Baltit Fort, Karimabad (Hunza); "
    "100 = Islamia College, Peshawar; 500 = Badshahi Mosque, Lahore; "
    "1000 = Faisal Mosque, Islamabad; 5000 = Iqbal Mausoleum, Lahore."
)

# Real State Bank of Pakistan issues. 200, 75 (the 2022 commemorative) and the
# still-circulating 5 were missing, so those notes were told "pehchan nahi ho
# saki" even though the model had read them correctly.
VALID_DENOMINATIONS = {
    "5",
    "10",
    "20",
    "50",
    "75",
    "100",
    "200",
    "500",
    "1000",
    "5000",
}
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

# No trailing \b: "PKR500" has no boundary between the code and the number, and
# requiring one lost the currency entirely. "Not followed by a letter" still
# keeps "PKRS" and "OTHERS" from matching.
_CURRENCY_CODE_RE = re.compile(
    r"\b(PKR|USD|GBP|INR|EUR|OTHER)(?![A-Za-z])", re.IGNORECASE
)
# "Line 1:", "1)", "2." - models label their own lines, and those labels are
# numbers that must not be mistaken for a denomination.
_LINE_LABEL_RE = re.compile(r"^(?:line\s*)?\d+\s*[:.)-]\s*", re.IGNORECASE)
_MAX_DENOMINATION_LENGTH = 5


def _clean_lines(reply):
    lines = []
    for line in (reply or "").splitlines():
        cleaned = _LINE_LABEL_RE.sub("", line.strip()).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def detect_currency_code(text):
    match = _CURRENCY_CODE_RE.search(text)
    if match:
        return match.group(1).upper()
    lowered = text.lower()
    if "dollar" in lowered:
        return "USD"
    if "pound" in lowered or "sterling" in lowered:
        return "GBP"
    if "euro" in lowered:
        return "EUR"
    # "Indian Rupee" has to be tested before the bare word "rupee", which in
    # this app's locale means Pakistani.
    if "indian" in lowered or "bharat" in lowered:
        return "INR"
    if re.search(r"\brs\b|rupee", lowered):
        return "PKR"
    return None


def extract_denominations(text):
    # "1,000" and "1 000" are one number, not two.
    joined = re.sub(r"(?<=\d)[,\s](?=\d{3}\b)", "", text)
    # Whole runs, then drop the absurd ones: truncating "10000" to "1000" would
    # turn a hallucinated value into a believable Pakistani note.
    found = re.findall(r"\d+", joined)
    return [
        token.lstrip("0") or "0"
        for token in found
        if len(token) <= _MAX_DENOMINATION_LENGTH
    ]


def parse_reply(reply):
    """Return (currency, denomination) from whatever shape the model used.

    The prompt asks for two bare lines, but models also answer "PKR 500",
    "Rs. 500", "Line 1: PKR / Line 2: 500", or wrap the pair in a sentence.
    Every one of those is a note the user was holding, and rejecting them used
    to tell the user the scan had failed.
    """
    lines = _clean_lines(reply)
    if not lines:
        return None, None
    blob = " ".join(lines)

    currency = detect_currency_code(blob)
    numbers = extract_denominations(blob)
    if not numbers:
        return currency, None
    if currency == "PKR":
        # Prose can carry stray numbers ("a 2015 series 500 rupee note"), so
        # prefer one that is actually a Pakistani denomination.
        valid = [number for number in numbers if number in VALID_DENOMINATIONS]
        return currency, (valid[0] if valid else numbers[0])
    return currency, numbers[0]


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

    meta = {}
    try:
        reply = gemini_client.generate(
            CURRENCY_PROMPT,
            image_data,
            image.mimetype or "image/jpeg",
            meta=meta,
            task="currency",
        )
        print(f"[currency] provider={meta.get('provider')} replied: {reply!r}", flush=True)
    except Exception as error:
        print(
            f"[currency] VISION API FAILURE ({type(error).__name__}): {error}",
            flush=True,
        )
        return scan_failed(503, SERVICE_DOWN_ERROR, SERVICE_DOWN_VOICE)

    lines = _clean_lines(reply)

    if not lines or lines[0].upper() == "UNCLEAR":
        return scan_failed(200, UNCLEAR_ERROR, UNCLEAR_VOICE)

    if lines[0].upper() == "NOT_CURRENCY":
        return scan_failed(200, NOT_CURRENCY_ERROR, NOT_CURRENCY_VOICE)

    currency, denomination = parse_reply(reply)

    if currency not in KNOWN_CURRENCIES:
        print(
            f"[currency] REJECTED: no usable currency code in {reply!r}", flush=True
        )
        return scan_failed(200, UNRECOGNIZED_ERROR, UNRECOGNIZED_VOICE)

    if (
        not denomination
        or not denomination.isdigit()
        or len(denomination) > _MAX_DENOMINATION_LENGTH
    ):
        print(
            f"[currency] REJECTED: no denomination for {currency} in {reply!r}",
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
    item_id = db.add_item("currency", name, "success")
    if item_id is None:
        # The note was read but the record did not land; tell the user rather
        # than showing a success card for a scan that is not in Meri List.
        print(f"[currency] DB FAILURE: {name} was not saved", flush=True)
        return scan_failed(500)

    return jsonify(
        {
            "id": item_id,
            "denomination": denomination,
            "currency": currency,
            "success": True,
            "provider": meta.get("provider"),
            "mock": bool(meta.get("mock")),
        }
    )
