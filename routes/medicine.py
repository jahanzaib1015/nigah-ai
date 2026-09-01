import re
from datetime import date, datetime

from flask import Blueprint, jsonify, request

import db
import gemini_client

MEDICINE_PROMPT = (
    "You are an expert on medicine packaging. The image may show a medicine BOX "
    "(carton label) or a BLISTER PACK (patta). For blister packs the text is "
    "usually printed on the back side (the silver foil side) - read whatever "
    "printed text is visible carefully. Packaging often mixes English with Urdu "
    "or other regional scripts - read every script that is present; the brand "
    "name itself may be printed in English, in Urdu, or in both. "
    "If the image is blurry, dark, empty, or the printed text cannot be read, "
    "reply with exactly: UNCLEAR. "
    "If the image is clear but does NOT show a medicine box or blister pack "
    "(for example a currency note, an object, a person), reply with exactly: NOT_MEDICINE. "
    "If it IS a medicine box or blister pack, reply with ONLY two lines: "
    "line 1 the medicine brand name FIRST, followed by its strength "
    "(for example 'Panadol 500mg' or 'Getformin 500mg'). "
    "The brand name is MANDATORY and must be read exactly and correctly as it "
    "is printed - never guess it. It is STRICTLY FORBIDDEN to leave line 1 "
    "empty or to reply with only a strength or unit (such as '500mg', '1 g', "
    "'10 ml') - such replies are invalid. Line 1 must ALWAYS start with the "
    "name, never with a number or a strength, and the name must never be "
    "skipped; if no brand is printed, use the generic / salt name printed on "
    "the pack (for example Paracetamol, Metformin); if neither is legible, "
    "use the most prominent printed product words on the pack as the name - "
    "never a bare strength. If the name is not obvious at first glance, look "
    "closer at the branding text printed on the box or on the foil back "
    "before answering. "
    "If the medicine has more than one strength (for example 10mg and 1000mg), "
    "write the strengths exactly as printed, joined with the '+' symbol, like "
    "'10mg + 1000mg' - keep the '+' symbol in your reply. "
    "Always write strengths with short unit symbols (mg, ml, mcg, g, IU) - "
    "never spell out words like 'milligram' or 'milligrams'. "
    "line 2 the expiry date in YYYY-MM-DD format, but ONLY if the pack clearly "
    "labels a date as the expiry (words such as EXP, Expiry, Expiry Date, Use "
    "Before, Best Before). Never treat batch numbers, lot numbers, "
    "manufacturing dates, prices, or unlabeled digits as an expiry date. If no "
    "clearly labeled expiry date is visible, reply EXPIRY_NOT_VISIBLE."
)

LABEL_PROMPT = (
    "You are reading a close-up photo of a medicine expiry label "
    "(from a medicine box or from the foil side of a blister pack). "
    "The label may mix English with Urdu or other regional scripts - read "
    "every script that is present. Look for the expiry date and the "
    "manufacturing date. Only read a date as the expiry date if it is clearly "
    "labeled as such (words such as EXP, Expiry, Expiry Date, Use Before, Best "
    "Before); never mistake a batch number, lot number, or manufacturing date "
    "for the expiry date. "
    "If the image is blank, blurry, dark, or shows no readable dates or text, "
    "reply with exactly: NO_DATES. "
    "Otherwise reply with ONLY two lines: "
    "line 1 the expiry date in YYYY-MM-DD format "
    "(or EXPIRY_NOT_VISIBLE if the expiry date is not visible), "
    "line 2 the manufacturing date in YYYY-MM-DD format "
    "(or MFG_NOT_VISIBLE if the manufacturing date is not visible)."
)

UNCLEAR_ERROR = (
    "Maazrat, likhayi parh nahi saki. Kripya check karein ke saaf side "
    "camera ke samne hai ya nahi, aur phir se koshish karein."
)
UNCLEAR_VOICE = (
    "معذرت، لکھائی پڑھ نہیں سکی۔ کرپیا چیک کریں کہ صاف سائیڈ کیمرے کے "
    "سامنے ہے یا نہیں، اور پھر سے کوشش کریں۔"
)

NO_DATES_ERROR = (
    "Label par dates nahi parh saki. Aap dawai ka naam dobara scan kar "
    "sakte hain ya skip kar sakte hain."
)
NO_DATES_VOICE = (
    "لیبل پر تاریخیں نہیں پڑھی جا سکیں۔ آپ دوائی کا نام دوبارہ اسکین کر "
    "سکتے ہیں یا اسکپ کر سکتے ہیں۔"
)

_STRENGTH_TOKEN = r"\d+(?:\.\d+)?\s*(?:mg|ml|mcg|ug|µg|g|iu)"
STRENGTH_ONLY_RE = re.compile(
    rf"^{_STRENGTH_TOKEN}(?:\s*(?:\+|plus|&|,)\s*{_STRENGTH_TOKEN})*$",
    re.IGNORECASE,
)

NAME_MISSING_ERROR = (
    "Dawai ka naam nahi parh saka. Brand name wali side camera ke samne "
    "rakhein aur dobara koshish karein."
)
NAME_MISSING_VOICE = (
    "دوائی کا نام نہیں پڑھ سکا۔ برانڈ نیم والی سائیڈ کیمرے کے سامنے "
    "رکھیں اور دوبارہ کوشش کریں۔"
)

medicine_bp = Blueprint("medicine", __name__)


def parse_date(value):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def voice_name(name):
    # Display text keeps the printed '+' icon; the spoken form says "plus".
    voice = re.sub(r"\s*\+\s*", " plus ", name)
    voice = re.sub(r"\bmilligrams?\b", "mg", voice, flags=re.IGNORECASE)
    return voice


_NAME_SENTINELS = {"", "UNKNOWN", "EXPIRY_NOT_VISIBLE", "N/A", "NA", "NONE", "-"}
_LEADING_STRENGTH_RE = re.compile(
    rf"^({_STRENGTH_TOKEN}(?:\s*(?:\+|plus|&|,)\s*{_STRENGTH_TOKEN})*)"
    rf"(?!\s*(?:\+|plus|&|,)\s*{_STRENGTH_TOKEN})\s+(.+)$",
    re.IGNORECASE,
)


def clean_name(raw):
    # Final parsing guard: returns a display-safe name, or None when the
    # model failed to provide one (empty, sentinel, or strength-only).
    name = (raw or "").strip().strip("'\"*`").strip()
    if not name or len(name) > 60 or name.upper() in _NAME_SENTINELS:
        return None
    # "500mg Panadol" -> "Panadol 500mg": the name must precede the strength.
    flipped = _LEADING_STRENGTH_RE.match(name)
    if flipped:
        name = f"{flipped.group(2).strip()} {flipped.group(1)}"
    if STRENGTH_ONLY_RE.match(name):
        return None
    return name


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
        return scan_failed(
            400,
            "Koi photo nahi mili. Dobara koshish karein.",
            "کوئی تصویر نہیں ملی۔ دوبارہ کوشش کریں۔",
        )

    image_data = image.read()
    if not image_data:
        return scan_failed()

    mime_type = image.mimetype or "image/jpeg"
    scan_type = (request.form.get("scan_type") or "medicine").strip().lower()

    if scan_type == "label":
        return detect_label(image_data, mime_type)

    try:
        response_text = gemini_client.generate(MEDICINE_PROMPT, image_data, mime_type)
        print(f"[medicine] gemini replied: {response_text!r}", flush=True)
    except Exception as error:
        print(f"Gemini API error: {error}", flush=True)
        return scan_failed(500)

    lines = [line.strip() for line in response_text.strip().splitlines() if line.strip()]

    if not lines or lines[0].upper() == "UNCLEAR":
        return scan_failed(200, UNCLEAR_ERROR, UNCLEAR_VOICE)

    if lines[0].upper() == "NOT_MEDICINE":
        return scan_failed(
            200,
            "Yeh dawai nahi hai. Sirf dawai ke box ya patte ki tasveer lein.",
            "یہ دوائی نہیں ہے۔ صرف دوائی کے باکس یا پتے کی تصویر لیں۔",
        )

    name = clean_name(lines[0])
    if name is None:
        stripped = (lines[0] or "").strip()
        if stripped and STRENGTH_ONLY_RE.match(stripped):
            return scan_failed(200, NAME_MISSING_ERROR, NAME_MISSING_VOICE)
        return scan_failed()

    expiry_raw = lines[1] if len(lines) > 1 else "EXPIRY_NOT_VISIBLE"
    expiry_date = (
        None if expiry_raw.upper() == "EXPIRY_NOT_VISIBLE" else parse_date(expiry_raw)
    )

    if expiry_date is not None:
        status = "expired" if expiry_date < date.today() else "safe"
    else:
        status = "unknown"

    item_id = db.add_item(
        "medicine", name, status, expiry_date.isoformat() if expiry_date else None
    )

    return jsonify(
        {
            "id": item_id,
            "name": name,
            "voice_name": voice_name(name),
            "status": status,
            "success": True,
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
        }
    )


def detect_label(image_data, mime_type):
    item_id_raw = request.form.get("item_id")
    try:
        item_id = int(item_id_raw) if item_id_raw else None
    except ValueError:
        item_id = None

    try:
        response_text = gemini_client.generate(LABEL_PROMPT, image_data, mime_type)
        print(f"[medicine] label gemini replied: {response_text!r}", flush=True)
    except Exception as error:
        print(f"Gemini API error: {error}", flush=True)
        return scan_failed(500)

    lines = [line.strip() for line in response_text.strip().splitlines() if line.strip()]

    if not lines or lines[0].upper() == "NO_DATES":
        return scan_failed(200, NO_DATES_ERROR, NO_DATES_VOICE)

    expiry_raw = lines[0]
    mfg_raw = lines[1] if len(lines) > 1 else "MFG_NOT_VISIBLE"

    expiry_date = (
        None if expiry_raw.upper() == "EXPIRY_NOT_VISIBLE" else parse_date(expiry_raw)
    )
    mfg_date = (
        None if mfg_raw.upper() == "MFG_NOT_VISIBLE" else parse_date(mfg_raw)
    )

    if expiry_date is None and mfg_date is None:
        return scan_failed(200, NO_DATES_ERROR, NO_DATES_VOICE)

    updates = {}
    status = None
    if expiry_date is not None:
        status = "expired" if expiry_date < date.today() else "safe"
        updates["expiry_date"] = expiry_date.isoformat()
        updates["status"] = status

    if item_id is not None and updates:
        db.update_item(item_id, **updates)

    return jsonify(
        {
            "success": True,
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
            "mfg_date": mfg_date.isoformat() if mfg_date else None,
            "status": status,
        }
    )
