import re
from datetime import date, datetime

from flask import Blueprint, jsonify, request

import db
import gemini_client

MEDICINE_PROMPT = (
    "You are an expert on medicine packaging. The image may show a medicine BOX "
    "(carton label) or a BLISTER PACK (patta). For blister packs the text is "
    "usually printed on the back side (the silver foil side) - read whatever "
    "printed text is visible carefully. "
    "If the image is blurry, dark, empty, or the printed text cannot be read, "
    "reply with exactly: UNCLEAR. "
    "If the image is clear but does NOT show a medicine box or blister pack "
    "(for example a currency note, an object, a person), reply with exactly: NOT_MEDICINE. "
    "If it IS a medicine box or blister pack, reply with ONLY two lines: "
    "line 1 the medicine brand name FIRST, followed by its strength "
    "(for example 'Panadol 500mg' or 'Getformin 500mg'). "
    "The brand name is MANDATORY: NEVER reply with only the strength such as "
    "'500mg'. If the name is not obvious at first glance, look closer at the "
    "branding text printed on the box or on the foil back before answering. "
    "line 2 the expiry date in YYYY-MM-DD format "
    "(or EXPIRY_NOT_VISIBLE if the expiry date is not visible or legible)."
)

LABEL_PROMPT = (
    "You are reading a close-up photo of a medicine expiry label "
    "(from a medicine box or from the foil side of a blister pack). "
    "Look for the expiry date and the manufacturing date. "
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

STRENGTH_ONLY_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:mg|ml|mcg|ug|µg|g|iu)$", re.IGNORECASE
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

    name = lines[0]
    if not name or name.upper() in ("UNKNOWN", "EXPIRY_NOT_VISIBLE") or len(name) > 60:
        return scan_failed()

    if STRENGTH_ONLY_RE.match(name):
        return scan_failed(200, NAME_MISSING_ERROR, NAME_MISSING_VOICE)

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
