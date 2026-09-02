import calendar
import re
from datetime import date, datetime

from flask import Blueprint, jsonify, request

import db
import gemini_client
from gemini_client import SERVICE_DOWN_ERROR, SERVICE_DOWN_VOICE

MEDICINE_PROMPT = (
    "You are an expert on medicine packaging. The image may show a medicine BOX "
    "(carton label) or a BLISTER PACK (patta). For blister packs the text is "
    "usually printed on the back side (the silver foil side) - read whatever "
    "printed text is visible carefully. Packaging often mixes English with Urdu "
    "or other regional scripts - read every script that is present; the brand "
    "name itself may be printed in English, in Urdu, or in both. "
    "If the image is blurry, dark, empty, or the printed text cannot be read, "
    "reply with exactly: UNCLEAR - but ONLY as a last resort, when truly "
    "nothing is readable: faint, stylized, or decorative brand text still "
    "counts if you can make out the letters. "
    "If the image is clear but does NOT show a medicine box or blister pack "
    "(for example a currency note, an object, a person), reply with exactly: NOT_MEDICINE. "
    "If it IS a medicine box or blister pack, reply with ONLY two lines: "
    "line 1 the medicine brand name FIRST, followed by its strength "
    "(for example 'Panadol 500mg' or 'Getformin 500mg'). "
    "The brand name is MANDATORY and must be read exactly and correctly as it "
    "is printed - never guess it, but search hard for it: the name may be in "
    "a stylized, decorative, or unusual font, may be faint or partially "
    "shadowed, and may sit among crowded marketing text, dosage charts, and "
    "regulatory markings. Scan the WHOLE pack (front, edges, and blister "
    "foil) and pick the most prominent pharmaceutical word - usually the "
    "largest product word, the word printed next to the strength, or the word "
    "repeated along the blister foil. It is STRICTLY FORBIDDEN to leave line 1 "
    "empty or to reply with only a strength, unit, or number (such as '500mg', "
    "'1 g', '10 ml', '125mg/5ml', or '500') - such replies are invalid. Line 1 "
    "must ALWAYS be the brand or generic name (optionally followed by its "
    "strength), never with a number or a strength first, and the name must "
    "never be skipped; if no brand is printed, use the main salt / generic "
    "name printed on the pack (for example Paracetamol, Metformin); if "
    "neither is legible, use the most prominent printed product words on the "
    "pack as the name - never a bare strength or number. Keep multi-word "
    "brand names together exactly as printed (for example 'Co-Amoxiclav "
    "625mg'); do not let marketing subtitles or taglines make you drop the "
    "name - read the brand, not the tagline. Keep line 1 concise: the brand "
    "or salt name plus strength only, without extra words like Tablet, "
    "Capsule, or Syrup unless they are part of the printed name itself. Do not "
    "prefix line 1 with 'Name:' or 'Line 1:' - write the name itself. "
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

NAME_MISSING_ERROR = (
    "Dawai ka naam nahi parh saka. Brand name wali side camera ke samne "
    "rakhein aur dobara koshish karein."
)
NAME_MISSING_VOICE = (
    "دوائی کا نام نہیں پڑھ سکا۔ برانڈ نیم والی سائیڈ کیمرے کے سامنے "
    "رکھیں اور دوبارہ کوشش کریں۔"
)

NO_PHOTO_ERROR = "Koi photo nahi mili. Dobara koshish karein."
NO_PHOTO_VOICE = "کوئی تصویر نہیں ملی۔ دوبارہ کوشش کریں۔"

_STRENGTH_TOKEN = (
    r"\d+(?:\.\d+)?\s*(?:milligrams?|milliliters?|millilitres?|micrograms?|"
    r"grams?|mcg|ug|µg|mg|ml|iu|g)"
)
_STRENGTH_CONNECTOR = r"(?:\+|plus|&|,|/)"
STRENGTH_ONLY_RE = re.compile(
    rf"^{_STRENGTH_TOKEN}(?:\s*{_STRENGTH_CONNECTOR}\s*{_STRENGTH_TOKEN})*$",
    re.IGNORECASE,
)


def _is_strength_only(value):
    # True when the string carries no real name: either a bare/compound
    # strength ("500mg", "10mg + 1000mg", "125mg/5ml", "500 milligram")
    # or only digits/symbols ("500", "500 + 250"). A valid name always
    # contains at least one letter in some script.
    text = (value or "").strip()
    if not text:
        return False
    if STRENGTH_ONLY_RE.match(text):
        return True
    return not re.search(r"[^\W\d_]", text)


medicine_bp = Blueprint("medicine", __name__)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# A date has to carry a separator or a month name. Bare digit runs stay
# unparseable on purpose - that is what stops a batch or lot number from being
# reported to a blind user as an expiry date.
_NUMERIC_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
)
_TEXTUAL_DATE_FORMATS = (
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%d %b, %Y", "%d %B, %Y",
    "%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y",
)
# The US month-first numeric form is deliberately absent: for an ambiguous
# numeric date it is safer to parse nothing (status "unknown") than to flip day
# and month and tell the user the wrong expiry.
_EXPIRY_LABEL_RE = re.compile(
    r"\b(?:expiry|expiration|expires|exp|use\s*before|best\s*before)\b[\s:._\-]*",
    re.IGNORECASE,
)
_MFG_LABEL_RE = re.compile(
    r"\b(?:mfg|mfd|manf|manufacturing|manufactured)\b[\s:._\-]*",
    re.IGNORECASE,
)
# Batch and lot markers are never dates, even when the value looks like one.
_BATCH_LABEL_RE = re.compile(
    r"\b(?:batch|lot|b[./\-]?no|serial|ref)\b[\s:._\-]*",
    re.IGNORECASE,
)
_WORD_DATE_RE = re.compile(r"^(?:(\d{1,2})[\s,\-/]*)?([A-Za-z]{3,9})\.?[\s,\-/]*(\d{2,4})$")
_MONTH_YEAR_RE = re.compile(r"^(\d{1,2})\s*[-/.]\s*(\d{4})$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})\s*[-/.]\s*(\d{1,2})$")
_BARE_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def _end_of_month(year, month):
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_date(value, allow_bare_year=False):
    """Parse a printed date in the shapes Pakistani packs actually use.

    The prompts ask for ISO, but models also return 03/2027, MAR-2027,
    31-12-2027 or "Expiry Date: 2027-03-31", and a date that was genuinely read
    must not be thrown away - it silently downgraded the medicine to "unknown".
    A month-only expiry resolves to the last day of that month, which is how
    pharmaceutical expiry dating works.
    """
    text = (value or "").strip()
    if not text:
        return None

    for fmt in _NUMERIC_DATE_FORMATS + _TEXTUAL_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    match = _WORD_DATE_RE.match(text)
    if match:
        month = _MONTHS.get(match.group(2).lower())
        if month:
            year = int(match.group(3))
            if year < 100:
                year += 2000 if year < 50 else 1900
            day = int(match.group(1)) if match.group(1) else None
            try:
                return date(year, month, day or _end_of_month(year, month).day)
            except ValueError:
                return None

    match = _MONTH_YEAR_RE.match(text)
    if match:
        month, year = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return _end_of_month(year, month)

    match = _YEAR_MONTH_RE.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return _end_of_month(year, month)

    # "EXP 2027" is common on blister foil, but a bare year is only trusted when
    # the pack labelled it as the expiry - otherwise it is just as likely to be
    # the start of a batch number.
    if allow_bare_year and _BARE_YEAR_RE.match(text):
        return _end_of_month(int(text), 12)

    return None


def read_date_line(line):
    """Return (parsed_date, kind) where kind is 'expiry', 'mfg', 'batch' or 'plain'.

    An explicitly manufacturing-labelled date is reported as 'mfg' so the caller
    can store it without ever passing it off as the expiry - that mistake would
    show a safe medicine as "Khatra! Expired". A batch or lot value is reported
    as no date at all.
    """
    text = (line or "").strip()
    if not text:
        return None, "plain"

    labelled_expiry = bool(_EXPIRY_LABEL_RE.search(text))
    if _BATCH_LABEL_RE.search(text) and not labelled_expiry:
        return None, "batch"
    labelled_mfg = bool(_MFG_LABEL_RE.search(text))

    cleaned = _EXPIRY_LABEL_RE.sub("", text)
    cleaned = _MFG_LABEL_RE.sub("", cleaned)
    cleaned = re.sub(r"\bdate\b[\s:._\-]*", "", cleaned, flags=re.IGNORECASE).strip()

    parsed = parse_date(cleaned, allow_bare_year=labelled_expiry)
    if parsed is None:
        return None, "plain"
    if labelled_mfg:
        return parsed, "mfg"
    if labelled_expiry:
        return parsed, "expiry"
    return parsed, "plain"


def read_dates(lines, second_is_mfg=False):
    """Pull (expiry, mfg) out of the reply's date lines.

    Labelled dates are always trusted. Unlabeled ones are read by position,
    which differs per prompt: the medicine scan sends a bare expiry first,
    while the label scan sends a bare expiry first AND a bare manufacturing
    date second (`second_is_mfg`). Anything unlabeled in a later position is
    ignored rather than guessed at.
    """
    expiry = None
    mfg = None
    for index, line in enumerate(lines):
        parsed, kind = read_date_line(line)
        if parsed is None:
            continue
        if kind == "mfg":
            if mfg is None:
                mfg = parsed
            continue
        if kind == "expiry":
            if expiry is None:
                expiry = parsed
            continue
        if index == 0:
            if expiry is None:
                expiry = parsed
        elif index == 1 and second_is_mfg:
            if mfg is None:
                mfg = parsed
    return expiry, mfg


def voice_name(name):
    # Display text keeps the printed '+' icon; the spoken form says "plus".
    voice = re.sub(r"\s*\+\s*", " plus ", name)
    voice = re.sub(r"\bmilligrams?\b", "mg", voice, flags=re.IGNORECASE)
    return voice


_NAME_SENTINELS = {"", "UNKNOWN", "EXPIRY_NOT_VISIBLE", "N/A", "NA", "NONE", "-"}
_LEADING_STRENGTH_RE = re.compile(
    rf"^({_STRENGTH_TOKEN}(?:\s*{_STRENGTH_CONNECTOR}\s*{_STRENGTH_TOKEN})*)"
    rf"(?!\s*{_STRENGTH_CONNECTOR}\s*{_STRENGTH_TOKEN})\s+(.+)$",
    re.IGNORECASE,
)
_NAME_LABEL_RE = re.compile(
    r"^(?:line\s*\d+|name|medicine|brand|product|dawai|label)\s*[:.\-)]\s*",
    re.IGNORECASE,
)
# Long multi-salt names are real ("Pantoprazole 40mg + Domperidone 30mg SR
# Capsules"), so the cap only has to catch a whole sentence.
_MAX_NAME_LENGTH = 120


def clean_name(raw):
    # Final parsing guard: returns a display-safe name, or None when the
    # model failed to provide one (empty, sentinel, strength-only, or a
    # bare number/symbol string with no letters).
    name = (raw or "").strip().strip("'\"*`").strip()
    name = _NAME_LABEL_RE.sub("", name).strip()
    if not name or len(name) > _MAX_NAME_LENGTH or name.upper() in _NAME_SENTINELS:
        return None
    # "500mg Panadol" -> "Panadol 500mg": the name must precede the strength.
    flipped = _LEADING_STRENGTH_RE.match(name)
    if flipped:
        name = f"{flipped.group(2).strip()} {flipped.group(1)}"
    if _is_strength_only(name):
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


def medicine_status(expiry_date):
    if expiry_date is None:
        return "unknown"
    return "expired" if expiry_date < date.today() else "safe"


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
        return scan_failed(400, NO_PHOTO_ERROR, NO_PHOTO_VOICE)

    image_data = image.read()
    if not image_data:
        print("[medicine] EMPTY UPLOAD: image field present but zero bytes", flush=True)
        return scan_failed(400, NO_PHOTO_ERROR, NO_PHOTO_VOICE)

    mime_type = image.mimetype or "image/jpeg"
    scan_type = (request.form.get("scan_type") or "medicine").strip().lower()

    if scan_type == "label":
        return detect_label(image_data, mime_type)

    meta = {}
    try:
        response_text = gemini_client.generate(
            MEDICINE_PROMPT, image_data, mime_type, meta=meta, task="medicine"
        )
        print(
            f"[medicine] provider={meta.get('provider')} replied: {response_text!r}",
            flush=True,
        )
    except Exception as error:
        print(
            f"[medicine] VISION API FAILURE ({type(error).__name__}): {error}",
            flush=True,
        )
        return scan_failed(503, SERVICE_DOWN_ERROR, SERVICE_DOWN_VOICE)

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
        print(
            f"[medicine] GUARD REJECTED name line {lines[0]!r} "
            f"(strength_only={_is_strength_only(lines[0])}, len={len(lines[0])})",
            flush=True,
        )
        return scan_failed(200, NAME_MISSING_ERROR, NAME_MISSING_VOICE)

    expiry_date, mfg_date = read_dates(lines[1:])
    status = medicine_status(expiry_date)

    item_id = db.add_item(
        "medicine",
        name,
        status,
        expiry_date.isoformat() if expiry_date else None,
        mfg_date.isoformat() if mfg_date else None,
        is_mock=bool(meta.get("mock")),
    )
    if item_id is None:
        print(f"[medicine] DB FAILURE: {name!r} was not saved", flush=True)
        return scan_failed(500)

    return jsonify(
        {
            "id": item_id,
            "name": name,
            "voice_name": voice_name(name),
            "status": status,
            "success": True,
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
            "mfg_date": mfg_date.isoformat() if mfg_date else None,
            "provider": meta.get("provider"),
            "mock": bool(meta.get("mock")),
            **gemini_client.mock_fields(meta),
        }
    )


def detect_label(image_data, mime_type):
    item_id_raw = request.form.get("item_id")
    try:
        item_id = int(item_id_raw) if item_id_raw else None
    except ValueError:
        item_id = None

    meta = {}
    try:
        response_text = gemini_client.generate(
            LABEL_PROMPT, image_data, mime_type, meta=meta, task="label"
        )
        print(
            f"[medicine] label provider={meta.get('provider')} replied: "
            f"{response_text!r}",
            flush=True,
        )
    except Exception as error:
        print(
            f"[medicine] label VISION API FAILURE ({type(error).__name__}): {error}",
            flush=True,
        )
        return scan_failed(503, SERVICE_DOWN_ERROR, SERVICE_DOWN_VOICE)

    lines = [line.strip() for line in response_text.strip().splitlines() if line.strip()]

    if not lines or lines[0].upper() == "NO_DATES":
        return scan_failed(200, NO_DATES_ERROR, NO_DATES_VOICE)

    # Line 1 is the expiry and line 2 the manufacturing date; a reply that
    # labels them explicitly is still read correctly.
    expiry_date, mfg_date = read_dates(lines, second_is_mfg=True)
    if expiry_date is None and mfg_date is None:
        print(f"[medicine] label REJECTED: no usable dates in {lines!r}", flush=True)
        return scan_failed(200, NO_DATES_ERROR, NO_DATES_VOICE)

    status = medicine_status(expiry_date) if expiry_date else None
    is_mock = bool(meta.get("mock"))

    if item_id is not None:
        row = db.get_item(item_id)
        if row is None:
            print(
                f"[medicine] DB FAILURE: label scan referenced missing item {item_id}",
                flush=True,
            )
            return scan_failed(500)
        if is_mock and not row["is_mock"]:
            # Synthetic dates must never overwrite dates read from a real pack:
            # a fabricated "safe" expiry on an expired medicine is the one
            # mistake this app cannot make.
            print(
                f"[medicine] label MOCK reply not written over real item {item_id}",
                flush=True,
            )
        else:
            # update_item() ignores None, so a label scan that only saw the
            # manufacturing date cannot erase a previously stored expiry.
            db.update_item(
                item_id,
                expiry_date=expiry_date.isoformat() if expiry_date else None,
                mfg_date=mfg_date.isoformat() if mfg_date else None,
                status=status,
            )

    return jsonify(
        {
            "success": True,
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
            "mfg_date": mfg_date.isoformat() if mfg_date else None,
            "status": status,
            "provider": meta.get("provider"),
            "mock": is_mock,
            **gemini_client.mock_fields(meta),
        }
    )
