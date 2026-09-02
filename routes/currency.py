import re

from flask import Blueprint, jsonify, request

import db
import gemini_client
from gemini_client import SCAN_FAILED_ERROR, SCAN_FAILED_VOICE

CURRENCY_PROMPT = (
    "You are an expert on Pakistani currency notes. The image may show EITHER "
    "face of a note - read the denomination number and the written words "
    "carefully. "
    "If the image is blurry, dark, empty, or no recognizable object is visible, "
    "reply with exactly: UNCLEAR. "
    "If the image is clear but does NOT show a Pakistani rupee note (for example "
    "a medicine, an object, a person, or the note of another country), reply "
    "with exactly: NOT_CURRENCY. "
    "If it IS a Pakistani rupee note, reply with ONLY two lines: "
    "line 1 exactly PKR; "
    "line 2 the denomination number exactly as printed (for example 500, 100, 20), "
    "with no currency symbol, no word 'rupees' and no other text on that line. "
    "Pakistani rupee notes are only issued in these denominations: 5, 10, 20, 50, "
    "75, 100, 200, 500, 1000, 5000 - line 2 MUST be exactly one of those numbers, "
    "so pick the closest one you can actually read on the note. "
    "Back-side landmarks: 10 = Bab-ul-Khyber (Khyber Pass gate); "
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

NOT_CURRENCY_ERROR = (
    "Yeh Pakistani currency note nahi hai. Sirf Pakistani note ki tasveer lein."
)
NOT_CURRENCY_VOICE = (
    "یہ پاکستانی کرنسی نوٹ نہیں ہے۔ صرف پاکستانی نوٹ کی تصویر لیں۔"
)

currency_bp = Blueprint("currency", __name__)

_PKR_RE = re.compile(r"\bPKR(?![A-Za-z])", re.IGNORECASE)
_RUPEE_RE = re.compile(r"\brs\b|\brupees?\b", re.IGNORECASE)
# Any other currency named in the reply means this is not a Pakistani note.
# Reporting a foreign note as PKR would be a fabricated answer, so a foreign
# marker always wins over a bare "rupee", which in this app's locale means
# Pakistani.
_FOREIGN_RE = re.compile(
    r"\b(?:USD|GBP|INR|EUR|OTHER|dollar|pound|sterling|euro|indian|bharat)\b",
    re.IGNORECASE,
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


def is_pkr(text):
    """True when the reply identifies a Pakistani rupee note and nothing else."""
    if _FOREIGN_RE.search(text):
        return False
    return bool(_PKR_RE.search(text)) or bool(_RUPEE_RE.search(text))


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


def _validated(reply):
    """(denomination, None) for a readable Pakistani note, else (None, why).

    The prompt asks for two bare lines, but models also answer "PKR 500",
    "Rs. 500", "Line 1: PKR / Line 2: 500", or wrap the pair in a sentence.
    Every one of those is a note the user was holding, so all of them are read.
    """
    lines = _clean_lines(reply)
    if not lines:
        return None, f"empty reply {reply!r}"
    blob = " ".join(lines)
    if not is_pkr(blob):
        return None, f"no Pakistani rupee note in {reply!r}"
    numbers = extract_denominations(blob)
    if not numbers:
        return None, f"no denomination in {reply!r}"
    # Prose can carry stray numbers ("a 2015 series 500 rupee note"), so prefer
    # one that is actually an issued denomination.
    valid = [number for number in numbers if number in VALID_DENOMINATIONS]
    denomination = valid[0] if valid else numbers[0]
    if denomination not in VALID_DENOMINATIONS:
        return None, f"{denomination!r} is not an issued PKR denomination"
    return denomination, None


def scan_failed(status=200, error=None, voice=None):
    return (
        jsonify(
            {
                "success": False,
                "error": error or SCAN_FAILED_ERROR,
                "voice": voice or SCAN_FAILED_VOICE,
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
            CURRENCY_PROMPT, image_data, image.mimetype or "image/jpeg", meta=meta
        )
        print(f"[currency] google replied: {reply!r}", flush=True)
    except Exception as error:
        print(
            f"[currency] VISION API FAILURE ({type(error).__name__}): {error}",
            flush=True,
        )
        return scan_failed(503, SCAN_FAILED_ERROR, SCAN_FAILED_VOICE)

    lines = _clean_lines(reply)

    if not lines or lines[0].upper() == "UNCLEAR":
        return scan_failed(200, UNCLEAR_ERROR, UNCLEAR_VOICE)

    if lines[0].upper() == "NOT_CURRENCY":
        return scan_failed(200, NOT_CURRENCY_ERROR, NOT_CURRENCY_VOICE)

    denomination, reason = _validated(reply)
    if reason:
        # The call succeeded but the answer is not a Pakistani note this app can
        # report. There is nothing to fall back to: the user is told the scan
        # failed rather than being handed an invented denomination.
        print(f"[currency] REJECTED: {reason}", flush=True)
        return scan_failed(200, SCAN_FAILED_ERROR, SCAN_FAILED_VOICE)

    name = f"Rs. {denomination}"
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
            "currency": "PKR",
            "success": True,
            "provider": meta.get("provider"),
        }
    )
