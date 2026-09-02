"""Offline end-to-end tests for the scan routes.

The vision call is stubbed and the database is a throwaway file, so these run
free and instantly and cannot pollute the developer's real Meri List or burn the
20-requests/day Google free tier. Unlike a pure unit test, the routes write to a
real SQLite file here, so "was it actually saved?" is part of what is verified.
"""
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TABI_API_KEY", "test-tabi-key")
os.environ.setdefault("TABI_BASE_URL", "https://tabi.invalid/v1")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ["MOCK_VISION"] = "0"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import db

_TEMP_HANDLE = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TEMP_HANDLE.close()
os.unlink(_TEMP_HANDLE.name)
db.DB_PATH = _TEMP_HANDLE.name

import gemini_client
from gemini_client import SERVICE_DOWN_ERROR, SERVICE_DOWN_VOICE
import app as app_module
from routes.currency import NOT_CURRENCY_ERROR, UNRECOGNIZED_ERROR
from routes.medicine import NAME_MISSING_ERROR, NO_DATES_ERROR, UNCLEAR_ERROR

CHECKS = []


def check(name):
    def decorate(fn):
        CHECKS.append((name, fn))
        return fn

    return decorate


class Stub:
    """Stand-in for gemini_client.generate."""

    def __init__(self, reply=None, error=None, provider="tabi", mock=False):
        self.reply = reply
        self.error = error
        self.provider = provider
        self.mock = mock
        self.calls = []

    def __call__(self, prompt, image_data=None, mime_type="image/jpeg", meta=None, task=None):
        self.calls.append({"task": task, "prompt": prompt, "bytes": len(image_data or b"")})
        if isinstance(meta, dict):
            meta["provider"] = self.provider
            meta["mock"] = self.mock
            meta["attempts"] = []
        if self.error is not None:
            raise self.error
        return self.reply


def install(stub):
    saved = gemini_client.generate
    gemini_client.generate = stub
    # The routes hold their own reference to the module, so patching the
    # attribute on gemini_client is enough for both.
    return lambda: setattr(gemini_client, "generate", saved)


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


# ---------------------------------------------------------------- currency ---

@check("currency: a valid note is reported and saved to Meri List")
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
        assert body["provider"] == "tabi", body
        assert body["mock"] is False, body
        assert stub.calls[0]["task"] == "currency", stub.calls

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
    restore = install(Stub(reply="Line 1: PKR\nLine 2: 1,000"))
    try:
        body = upload("/detect-currency").get_json()
        assert body["success"] is True, body
        assert body["denomination"] == "1000", body
    finally:
        restore()


@check("currency: an impossible denomination is refused and not saved")
def t_currency_invalid_denomination():
    reset_db()
    restore = install(Stub(reply="PKR\n300"))
    try:
        response = upload("/detect-currency")
        assert response.status_code == 200, response.status_code
        body = response.get_json()
        assert body["success"] is False, body
        assert body["error"] == UNRECOGNIZED_ERROR, body
        assert db.get_items() == [], "a refused scan must not be saved"
    finally:
        restore()


@check("currency: UNCLEAR / NOT_CURRENCY keep their own messages")
def t_currency_sentinels():
    reset_db()
    for reply, expected in (("UNCLEAR", None), ("NOT_CURRENCY", NOT_CURRENCY_ERROR)):
        restore = install(Stub(reply=reply))
        try:
            body = upload("/detect-currency").get_json()
            assert body["success"] is False, body
            if expected:
                assert body["error"] == expected, body
            assert body["voice"], body
        finally:
            restore()
    assert db.get_items() == []


# ----------------------------------------------------------------- failures ---

@check("both providers down: 503 with the spoken service message, nothing saved")
def t_service_down():
    reset_db()
    error = gemini_client.VisionUnavailable(
        "no vision provider succeeded (tabi=RuntimeError, google=ResourceExhausted)"
    )
    for path, task in (("/detect-currency", "currency"), ("/detect-medicine", "medicine")):
        restore = install(Stub(error=error))
        try:
            response = upload(path)
            assert response.status_code == 503, (path, response.status_code)
            body = response.get_json()
            assert body["success"] is False, body
            assert body["error"] == SERVICE_DOWN_ERROR, body
            assert body["voice"] == SERVICE_DOWN_VOICE, body
        finally:
            restore()
    assert db.get_items() == [], "a failed scan must never create a row"


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
        assert body["voice_name"] == "Panadol 500mg", body
        assert body["status"] == "safe", body
        assert body["expiry_date"] == "2027-03-31", body
        assert body["mfg_date"] is None, body

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
        assert body["voice_name"] == "Getformin 2mg plus 500mg", body
        assert db.get_item(body["id"])["status"] == "expired"
    finally:
        restore()


@check("medicine: a strength-only reply is refused and not saved")
def t_medicine_name_guard():
    reset_db()
    for reply in ("500mg", "500", "10mg + 1000mg", "UNKNOWN"):
        restore = install(Stub(reply=reply))
        try:
            response = upload("/detect-medicine")
            assert response.status_code == 200, response.status_code
            body = response.get_json()
            assert body["success"] is False, (reply, body)
            assert body["error"] == NAME_MISSING_ERROR, (reply, body)
        finally:
            restore()
    assert db.get_items() == [], "no bare strength may ever be saved"


@check("medicine: UNCLEAR keeps its own message")
def t_medicine_unclear():
    reset_db()
    restore = install(Stub(reply="UNCLEAR"))
    try:
        body = upload("/detect-medicine").get_json()
        assert body["success"] is False, body
        assert body["error"] == UNCLEAR_ERROR, body
        assert db.get_items() == []
    finally:
        restore()


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


@check("label scan: no dates at all is a refusal, not a silent success")
def t_label_scan_no_dates():
    reset_db()
    restore = install(Stub(reply="Panadol 500mg\n2027-03-31"))
    try:
        item_id = upload("/detect-medicine").get_json()["id"]
    finally:
        restore()

    for reply in ("NO_DATES", "Batch: 45452", "nothing readable"):
        restore = install(Stub(reply=reply))
        try:
            body = upload(
                "/detect-medicine", extra={"scan_type": "label", "item_id": str(item_id)}
            ).get_json()
            assert body["success"] is False, (reply, body)
            assert body["error"] == NO_DATES_ERROR, (reply, body)
        finally:
            restore()
    assert db.get_item(item_id)["expiry_date"] == "2027-03-31"


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


@check("the mock provider is flagged in the response, never silent")
def t_mock_flag_reaches_client():
    reset_db()
    restore = install(Stub(reply="PKR\n500", provider="mock", mock=True))
    try:
        body = upload("/detect-currency").get_json()
        assert body["success"] is True, body
        assert body["mock"] is True, body
        assert body["provider"] == "mock", body
    finally:
        restore()


@check("/health reports the whole chain")
def t_health():
    body = client().get("/health")
    assert body.status_code == 200, body.status_code
    text = body.get_data(as_text=True)
    assert "providers=" in text, text
    assert "mock=off" in text, text


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
