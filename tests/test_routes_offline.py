"""Offline end-to-end tests for the scan routes.

The vision call is stubbed and the database is a throwaway file, so these run
free and instantly and cannot pollute the developer's real Meri List or burn the
20-requests/day Google free tier. Unlike a pure unit test, the routes write to a
real SQLite file here, so "was it actually saved?" is part of what is verified.

The contract under test is binary: either Google Gemini read the photo and the
validators accept what it said, or the user is told the scan failed. There is no
third answer, and nothing in this app may invent a note or a medicine.
"""
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# gemini_client refuses to import without a key.
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import db

_TEMP_HANDLE = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TEMP_HANDLE.close()
os.unlink(_TEMP_HANDLE.name)
db.DB_PATH = _TEMP_HANDLE.name

import gemini_client
from gemini_client import SCAN_FAILED_ERROR, SCAN_FAILED_VOICE
import app as app_module
from routes.currency import NOT_CURRENCY_ERROR
from routes.currency import UNCLEAR_ERROR as CURRENCY_UNCLEAR_ERROR
from routes.medicine import (
    NOT_MEDICINE_ERROR,
    NO_DATES_ERROR,
    UNCLEAR_ERROR,
)

CHECKS = []


def check(name):
    def decorate(fn):
        CHECKS.append((name, fn))
        return fn

    return decorate


# Every field any endpoint is allowed to return, and every column a Meri List
# row is allowed to carry. Anything outside these was not read from a photo.
RESPONSE_KEYS = {
    "success",
    "error",
    "voice",
    "id",
    "provider",
    "currency",
    "denomination",
    "name",
    "voice_name",
    "status",
    "expiry_date",
    "mfg_date",
    "items",
}
ROW_KEYS = {"id", "type", "name", "status", "expiry_date", "mfg_date", "timestamp"}


class Stub:
    """Stand-in for gemini_client.generate."""

    def __init__(self, reply=None, error=None, provider="google"):
        self.reply = reply
        self.error = error
        self.provider = provider
        self.calls = []

    def __call__(self, prompt, image_data=None, mime_type="image/jpeg", meta=None):
        self.calls.append({"prompt": prompt, "bytes": len(image_data or b"")})
        if isinstance(meta, dict):
            meta.clear()
            meta["provider"] = self.provider
            meta["seconds"] = 1.0
        if self.error is not None:
            raise self.error
        return self.reply


def install(stub):
    saved = gemini_client.generate
    gemini_client.generate = stub
    # The routes hold their own reference to the module, so patching the
    # attribute on gemini_client is enough for both.
    return lambda: setattr(gemini_client, "generate", saved)


def outage():
    """Break the REAL client, below the route boundary.

    Nothing is stubbed here: generate() runs its own retry loop, the transport
    raises, and the client has to turn that into VisionUnavailable so the route
    answers with the honest failure instead of a fabricated result.
    """
    def down(*args):
        raise RuntimeError("transport is down")

    saved = gemini_client._call_google
    gemini_client._call_google = down
    return lambda: setattr(gemini_client, "_call_google", saved)


def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def upload(path, extra=None):
    data = {"image": (io.BytesIO(b"\xff\xd8\xff\xd9fakejpegbytes"), "scan.jpg")}
    if extra:
        data.update(extra)
    return client().post(path, data=data, content_type="multipart/form-data")


def reset_db():
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM scanned_items")
        conn.commit()
    finally:
        conn.close()


def assert_honest_failure(response, expected_error=SCAN_FAILED_ERROR, expected_voice=None):
    """The one acceptable shape for any scan this app cannot really answer."""
    body = response.get_json()
    assert body["success"] is False, body
    assert body["error"] == expected_error, body
    if expected_voice is None:
        # The on-screen line is Roman Urdu, the spoken line Urdu script; a route
        # that returned one for both would leave the screen reader silent.
        voice = body["voice"]
        assert voice and voice != body["error"], body
        assert any("\u0600" <= char <= "\u06ff" for char in voice), body
    else:
        assert body["voice"] == expected_voice, body
    # A failed scan has nothing to report, so no result fields may leak in.
    for key in ("denomination", "currency", "name", "expiry_date", "mfg_date"):
        assert key not in body, body
    return body


# ---------------------------------------------------------------- currency ---

@check("currency: a valid Pakistani note is reported and saved to Meri List")
def t_currency_saved():
    reset_db()
    stub = Stub(reply="PKR\n200")
    restore = install(stub)
    try:
        response = upload("/detect-currency")
        assert response.status_code == 200, response.status_code
        body = response.get_json()
        assert body["success"] is True, body
        assert body["denomination"] == "200", body
        assert body["currency"] == "PKR", body
        assert body["provider"] == "google", body
        assert stub.calls[0]["bytes"] > 0, stub.calls

        rows = db.get_items()
        assert len(rows) == 1, rows
        assert rows[0]["name"] == "Rs. 200", rows
        assert rows[0]["type"] == "currency", rows
        assert rows[0]["status"] == "success", rows
        assert rows[0]["id"] == body["id"], (rows, body)
    finally:
        restore()


@check("currency: a previously-rejected real note (Rs. 200/75/5) now works")
def t_currency_new_denominations():
    reset_db()
    for denomination in ("200", "75", "5"):
        restore = install(Stub(reply=f"PKR\n{denomination}"))
        try:
            body = upload("/detect-currency").get_json()
            assert body["success"] is True, (denomination, body)
            assert body["denomination"] == denomination, body
        finally:
            restore()
    names = [row["name"] for row in db.get_items()]
    assert names == ["Rs. 5", "Rs. 75", "Rs. 200"], names


@check("currency: a sloppier reply shape is still read")
def t_currency_sloppy_reply():
    reset_db()
    for reply, expected in (
        ("Line 1: PKR\nLine 2: 1,000", "1000"),
        ("PKR 500", "500"),
        ("Rs. 50", "50"),
        ("This is a Pakistani 100 rupee note", "100"),
    ):
        restore = install(Stub(reply=reply))
        try:
            body = upload("/detect-currency").get_json()
            assert body["success"] is True, (reply, body)
            assert body["denomination"] == expected, (reply, body)
        finally:
            restore()


@check("currency: an impossible denomination is refused and not saved")
def t_currency_invalid_denomination():
    reset_db()
    for reply in ("PKR\n300", "PKR\n0", "PKR\n12345", "PKR"):
        restore = install(Stub(reply=reply))
        try:
            response = upload("/detect-currency")
            assert response.status_code == 200, (reply, response.status_code)
            assert_honest_failure(response)
        finally:
            restore()
    assert db.get_items() == [], "a refused scan must not be saved"


@check("currency: a foreign note is never reported as Pakistani")
def t_currency_pkr_only():
    """Scope is PKR only. Calling an Indian or US note 'Rs. 500' would itself be
    a fabricated answer, so a foreign marker always defeats a bare 'rupee'."""
    reset_db()
    for reply in (
        "INR\n500",
        "USD\n100",
        "GBP\n20",
        "EUR\n50",
        "Indian Rupee 500",
        "Rs. 500 Indian currency note",
        "OTHER\n500",
        "500 dollars",
    ):
        restore = install(Stub(reply=reply))
        try:
            response = upload("/detect-currency")
            assert response.status_code == 200, (reply, response.status_code)
            assert_honest_failure(response)
        finally:
            restore()
    assert db.get_items() == [], "no foreign note may be saved as PKR"


@check("currency: UNCLEAR / NOT_CURRENCY keep their own messages")
def t_currency_sentinels():
    reset_db()
    for reply, expected in (
        ("UNCLEAR", CURRENCY_UNCLEAR_ERROR),
        ("NOT_CURRENCY", NOT_CURRENCY_ERROR),
    ):
        restore = install(Stub(reply=reply))
        try:
            response = upload("/detect-currency")
            assert response.status_code == 200, (reply, response.status_code)
            # The sentinel is the model's real verdict about the photo, so it
            # keeps its own message - but it is still a failure.
            assert_honest_failure(response, expected)
        finally:
            restore()
    assert db.get_items() == []


# ----------------------------------------------------------------- failures ---

@check("the real client failing gives a 503 and the honest Urdu message")
def t_service_down():
    reset_db()
    restore = outage()
    try:
        for path, extra in (
            ("/detect-currency", None),
            ("/detect-medicine", None),
            ("/detect-medicine", {"scan_type": "label", "item_id": "1"}),
        ):
            response = upload(path, extra)
            assert response.status_code == 503, (path, extra, response.status_code)
            assert_honest_failure(response, SCAN_FAILED_ERROR, SCAN_FAILED_VOICE)
    finally:
        restore()
    assert db.get_items() == [], "a failed scan must never create a row"


@check("VisionUnavailable from any layer is the same honest failure")
def t_vision_unavailable_is_uniform():
    reset_db()
    error = gemini_client.VisionUnavailable("google gemini failed (DeadlineExceeded)")
    for path in ("/detect-currency", "/detect-medicine"):
        restore = install(Stub(error=error))
        try:
            response = upload(path)
            assert response.status_code == 503, (path, response.status_code)
            assert_honest_failure(response)
        finally:
            restore()
    assert db.get_items() == []


@check("missing and empty uploads get a 400, not the generic failure")
def t_bad_uploads():
    reset_db()
    response = client().post("/detect-currency", data={}, content_type="multipart/form-data")
    assert response.status_code == 400, response.status_code
    assert response.get_json()["success"] is False

    empty = {"image": (io.BytesIO(b""), "scan.jpg")}
    response = client().post(
        "/detect-medicine", data=empty, content_type="multipart/form-data"
    )
    assert response.status_code == 400, response.status_code
    assert db.get_items() == []


# ---------------------------------------------------------------- medicine ---

@check("medicine: name, strength and expiry are saved together")
def t_medicine_saved():
    reset_db()
    restore = install(Stub(reply="Panadol 500mg\n2027-03-31"))
    try:
        response = upload("/detect-medicine")
        assert response.status_code == 200, response.status_code
        body = response.get_json()
        assert body["success"] is True, body
        assert body["name"] == "Panadol 500mg", body
        assert body["voice_name"] == "پانادول 500mg", body
        assert body["status"] == "safe", body
        assert body["expiry_date"] == "2027-03-31", body
        assert body["mfg_date"] is None, body
        assert body["provider"] == "google", body

        row = db.get_item(body["id"])
        assert row["name"] == "Panadol 500mg", row
        assert row["expiry_date"] == "2027-03-31", row
        assert row["status"] == "safe", row
    finally:
        restore()


@check("medicine: a manufacturing date is stored, never shown as the expiry")
def t_medicine_mfg_not_expiry():
    reset_db()
    restore = install(Stub(reply="Panadol 500mg\nMFG: 2024-01-15"))
    try:
        body = upload("/detect-medicine").get_json()
        assert body["success"] is True, body
        assert body["expiry_date"] is None, body
        assert body["status"] == "unknown", body
        assert body["mfg_date"] == "2024-01-15", body

        row = db.get_item(body["id"])
        assert row["expiry_date"] is None, row
        assert row["mfg_date"] == "2024-01-15", row
        assert row["status"] == "unknown", row
    finally:
        restore()


@check("medicine: a non-ISO printed date is still understood")
def t_medicine_non_iso_date():
    reset_db()
    for reply, expected in (
        ("Brufen 400mg\n03/2027", "2027-03-31"),
        ("Brufen 400mg\n31-12-2027", "2027-12-31"),
        ("Brufen 400mg\nExpiry Date: MAR 2027", "2027-03-31"),
    ):
        restore = install(Stub(reply=reply))
        try:
            body = upload("/detect-medicine").get_json()
            assert body["success"] is True, (reply, body)
            assert body["expiry_date"] == expected, (reply, body)
            assert body["status"] == "safe", (reply, body)
        finally:
            restore()


@check("medicine: an expiry in the past warns the user")
def t_medicine_expired():
    reset_db()
    restore = install(Stub(reply="Getformin 2mg + 500mg\n2020-01-01"))
    try:
        body = upload("/detect-medicine").get_json()
        assert body["status"] == "expired", body
        assert body["name"] == "Getformin 2mg + 500mg", body
        assert body["voice_name"] == "گیتفورمین 2mg plus 500mg", body
        assert db.get_item(body["id"])["status"] == "expired"
    finally:
        restore()


@check("medicine: a reply with no usable name is refused and not saved")
def t_medicine_name_guard():
    reset_db()
    for reply in (
        "500mg",
        "500",
        "10mg + 1000mg",
        "UNKNOWN",
        # A refusal sentence is made of letters, so the strength and length
        # guards let it through; it used to be SAVED as the name and spoken.
        "sorry, the packaging text is cut off",
        "I cannot read the medicine name from this image",
    ):
        restore = install(Stub(reply=reply))
        try:
            response = upload("/detect-medicine")
            assert response.status_code == 200, (reply, response.status_code)
            assert_honest_failure(response)
        finally:
            restore()
    assert db.get_items() == [], "no bare strength or refusal may ever be saved"


@check("medicine: UNCLEAR / NOT_MEDICINE keep their own messages")
def t_medicine_unclear():
    reset_db()
    for reply, expected in (
        ("UNCLEAR", UNCLEAR_ERROR),
        ("NOT_MEDICINE", NOT_MEDICINE_ERROR),
    ):
        restore = install(Stub(reply=reply))
        try:
            response = upload("/detect-medicine")
            assert response.status_code == 200, (reply, response.status_code)
            assert_honest_failure(response, expected)
        finally:
            restore()
    assert db.get_items() == []


# ------------------------------------------------------------- label scan ---

@check("label scan: expiry and manufacturing dates both merge into the row")
def t_label_scan_merges():
    reset_db()
    restore = install(Stub(reply="Panadol 500mg\nEXPIRY_NOT_VISIBLE"))
    try:
        body = upload("/detect-medicine").get_json()
    finally:
        restore()
    assert body["status"] == "unknown", body
    item_id = body["id"]
    assert db.get_item(item_id)["expiry_date"] is None

    restore = install(Stub(reply="2027-03-31\n2024-01-15"))
    try:
        response = upload(
            "/detect-medicine", extra={"scan_type": "label", "item_id": str(item_id)}
        )
        assert response.status_code == 200, response.status_code
        body = response.get_json()
        assert body["success"] is True, body
        assert body["expiry_date"] == "2027-03-31", body
        assert body["mfg_date"] == "2024-01-15", body
        assert body["status"] == "safe", body
    finally:
        restore()

    row = db.get_item(item_id)
    assert row["expiry_date"] == "2027-03-31", row
    assert row["mfg_date"] == "2024-01-15", row
    assert row["status"] == "safe", row
    assert row["name"] == "Panadol 500mg", row


@check("label scan: seeing only a manufacturing date cannot erase the expiry")
def t_label_scan_keeps_expiry():
    reset_db()
    restore = install(Stub(reply="Panadol 500mg\n2027-03-31"))
    try:
        item_id = upload("/detect-medicine").get_json()["id"]
    finally:
        restore()

    restore = install(Stub(reply="EXPIRY_NOT_VISIBLE\n2024-01-15"))
    try:
        body = upload(
            "/detect-medicine", extra={"scan_type": "label", "item_id": str(item_id)}
        ).get_json()
        assert body["success"] is True, body
        assert body["expiry_date"] is None, body
        assert body["mfg_date"] == "2024-01-15", body
        assert body["status"] is None, body
    finally:
        restore()

    row = db.get_item(item_id)
    assert row["expiry_date"] == "2027-03-31", "stored expiry must survive"
    assert row["status"] == "safe", row
    assert row["mfg_date"] == "2024-01-15", row


@check("label scan: no usable dates is a refusal, not a silent success")
def t_label_scan_no_dates():
    reset_db()
    restore = install(Stub(reply="Panadol 500mg\n2027-03-31"))
    try:
        item_id = upload("/detect-medicine").get_json()["id"]
    finally:
        restore()

    # NO_DATES is the model's verdict about the photo; the other two are replies
    # it produced that carry no date at all. Both must fail, neither may guess.
    for reply, expected in (
        ("NO_DATES", NO_DATES_ERROR),
        ("Batch: 45452", SCAN_FAILED_ERROR),
        ("nothing readable", SCAN_FAILED_ERROR),
    ):
        restore = install(Stub(reply=reply))
        try:
            response = upload(
                "/detect-medicine", extra={"scan_type": "label", "item_id": str(item_id)}
            )
            assert response.status_code == 200, (reply, response.status_code)
            assert_honest_failure(response, expected)
        finally:
            restore()

    row = db.get_item(item_id)
    assert row["expiry_date"] == "2027-03-31", row
    assert row["mfg_date"] is None, row
    assert row["status"] == "safe", row


@check("label scan: a missing item is a 500, never an invented success")
def t_label_scan_missing_item():
    reset_db()
    restore = install(Stub(reply="2027-03-31\n2024-01-15"))
    try:
        response = upload(
            "/detect-medicine", extra={"scan_type": "label", "item_id": "999"}
        )
        assert response.status_code == 500, response.status_code
        assert response.get_json()["success"] is False, response.get_json()
    finally:
        restore()
    assert db.get_items() == []


# ------------------------------------------------------------- meri list ---

@check("Meri List serves the saved dates and delete removes the row")
def t_meri_list():
    reset_db()
    restore = install(Stub(reply="Panadol 500mg\n2027-03-31\nMFG: 2024-01-15"))
    try:
        body = upload("/detect-medicine").get_json()
    finally:
        restore()
    item_id = body["id"]
    assert body["mfg_date"] == "2024-01-15", body

    listing = client().get("/meri-list").get_json()
    assert listing["success"] is True, listing
    assert len(listing["items"]) == 1, listing
    row = listing["items"][0]
    assert row["name"] == "Panadol 500mg", row
    assert row["expiry_date"] == "2027-03-31", row
    assert row["mfg_date"] == "2024-01-15", row
    assert row["status"] == "safe", row
    assert row["timestamp"], row

    deleted = client().delete(f"/meri-list/{item_id}")
    assert deleted.get_json()["success"] is True
    assert client().get("/meri-list").get_json()["items"] == []
    assert client().delete(f"/meri-list/{item_id}").status_code == 404


@check("no API response can carry a field this app cannot honestly fill")
def t_response_surface():
    """Pinned to an allowlist rather than a denylist: if an endpoint ever grows
    a field that lets a scan report something the model did not read, this fails
    until the author looks at it."""
    reset_db()
    responses = []
    restore = install(Stub(reply="PKR\n1000"))
    try:
        responses.append(upload("/detect-currency"))
    finally:
        restore()
    restore = install(Stub(reply="Panadol 500mg\n2027-03-31"))
    try:
        responses.append(upload("/detect-medicine"))
    finally:
        restore()
    restore = outage()
    try:
        responses.append(upload("/detect-currency"))
        responses.append(upload("/detect-medicine"))
    finally:
        restore()
    responses.append(client().get("/meri-list"))
    responses.append(client().get("/health"))

    for response in responses:
        text = response.get_data(as_text=True)
        for badge in ("TEST DATA", "asli pehchan nahi hui"):
            assert badge not in text, (badge, text)
        if not text.startswith("{"):
            continue  # /health is plain text
        body = response.get_json()
        keys = set(body)
        if "items" in keys:
            for row in body["items"]:
                assert set(row) <= ROW_KEYS, (set(row) - ROW_KEYS, row)
            keys -= {"items"}
        assert keys <= RESPONSE_KEYS, (keys - RESPONSE_KEYS, body)
        if body.get("success") and "provider" in body:
            assert body["provider"] == "google", body


@check("photo sentinels keep their own message and are never a fake result")
def t_sentinels_stay_failures():
    """UNCLEAR / NOT_CURRENCY / NOT_MEDICINE / NO_DATES are the model's judgement
    about the PHOTO, not a service failure. They still have to be failures:
    inventing a note the user is not holding is worse than asking for a rescan."""
    reset_db()
    cases = (
        ("/detect-currency", None, "UNCLEAR", CURRENCY_UNCLEAR_ERROR),
        ("/detect-currency", None, "NOT_CURRENCY", NOT_CURRENCY_ERROR),
        ("/detect-medicine", None, "UNCLEAR", UNCLEAR_ERROR),
        ("/detect-medicine", None, "NOT_MEDICINE", NOT_MEDICINE_ERROR),
        ("/detect-medicine", {"scan_type": "label"}, "NO_DATES", NO_DATES_ERROR),
    )
    for path, extra, reply, expected in cases:
        restore = install(Stub(reply=reply))
        try:
            response = upload(path, extra)
            assert response.status_code == 200, (reply, response.status_code)
            assert_honest_failure(response, expected)
        finally:
            restore()
    assert db.get_items() == [], "a photo the model could not read must not create a row"


@check("/health reports one provider, the model and no fallback")
def t_health():
    body = client().get("/health")
    assert body.status_code == 200, body.status_code
    text = body.get_data(as_text=True)
    assert "provider=google" in text, text
    assert "model=gemini-3.6-flash" in text, text
    assert "fallback=none" in text, text


def main():
    failures = []
    try:
        db.init_db()
        for name, fn in CHECKS:
            try:
                fn()
            except Exception as error:
                failures.append(name)
                print(f"FAIL  {name}\n      {type(error).__name__}: {error}")
            else:
                print(f"PASS  {name}")
    finally:
        try:
            os.unlink(db.DB_PATH)
        except OSError:
            pass
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(db.DB_PATH + suffix)
            except OSError:
                pass
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
