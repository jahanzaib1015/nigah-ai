"""Offline tests for reply parsing (currency + medicine) and the SQLite layer.

No network calls and no live model: these exercise the parsing and persistence
logic that decides what a blind user is told and what lands in Meri List.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("GEMINI_API_KEY", "test-google-key")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import db
from routes.currency import VALID_DENOMINATIONS, _validated, is_pkr
from routes.medicine import (
    clean_name,
    medicine_status,
    parse_date,
    read_date_line,
    read_dates,
    voice_name,
)

CHECKS = []


def check(name):
    def decorate(fn):
        CHECKS.append((name, fn))
        return fn

    return decorate


# ---------------------------------------------------------------- currency ---

# Every one of these is a Pakistani note the user was actually holding; each
# used to be rejected unless the model happened to use the exact two-bare-lines
# shape. The value is the denomination the route will announce and save.
CURRENCY_ACCEPTED = [
    ("PKR\n500", "500"),
    ("PKR\n1000", "1000"),
    ("PKR 500", "500"),
    ("PKR500", "500"),
    ("Rs. 500", "500"),
    ("Rs 200", "200"),
    ("Line 1: PKR\nLine 2: 500", "500"),
    ("1. PKR\n2. 100", "100"),
    ("PKR\n1,000", "1000"),
    ("This is a Pakistani 500 rupee note", "500"),
    ("A 2015 series 500 rupee note", "500"),
    ("PKR\n75", "75"),
    ("PKR\n5", "5"),
    ("  PKR  \n  500  ", "500"),
    ("PKR\n500\n(note is slightly worn)", "500"),
]

CURRENCY_REJECTED = [
    "",
    "   ",
    "I cannot identify this image",
    "PKR",          # no denomination at all
    "note of currency",
    "PKR\n10000",   # not a real note, and must not be truncated to 1000
    "PKR\n999999",
    "PKR\n300",
    "PKR\n0",
]

# Scope is Pakistani rupees only. Announcing a foreign note as "Rs. 500" would
# be a fabricated answer, so these all have to be refused.
FOREIGN_REJECTED = [
    "USD\n100",
    "GBP\n20",
    "EUR\n50",
    "INR\n500",
    "Indian Rupee\n500",
    "OTHER\n250",
    "500 dollars",
    "20 pound note",
    "Rs. 500 Indian currency note",
    "PKR 500 or USD 100",
]


def route_would_accept(reply):
    """Mirror the checks detect_currency applies before it saves a scan."""
    denomination, reason = _validated(reply)
    return reason is None and denomination in VALID_DENOMINATIONS


@check("currency: real Pakistani replies parse in every shape the model uses")
def t_currency_accepted():
    for reply, expected in CURRENCY_ACCEPTED:
        assert _validated(reply) == (expected, None), f"{reply!r} -> {_validated(reply)!r}"
        assert route_would_accept(reply), f"{reply!r} would be refused by the route"


@check("currency: junk and impossible notes are refused, not saved")
def t_currency_rejected():
    for reply in CURRENCY_REJECTED:
        assert not route_would_accept(reply), (
            f"{reply!r} would be accepted as {_validated(reply)!r}"
        )


@check("currency: a foreign note is never announced as Pakistani rupees")
def t_currency_pkr_only():
    for reply in FOREIGN_REJECTED:
        assert not is_pkr(reply), f"{reply!r} was read as a Pakistani note"
        assert not route_would_accept(reply), (
            f"{reply!r} would be accepted as {_validated(reply)!r}"
        )
    # A bare "rupee"/"Rs." is Pakistani in this app's locale, but only when
    # nothing else in the reply says otherwise.
    assert is_pkr("Rs. 500"), "a plain rupee note must still be readable"
    assert is_pkr("PKR 500")
    assert not is_pkr("Indian rupee 500")
    assert not is_pkr("USD 100")


@check("currency: 10000 is not read as a valid 1000 note")
def t_no_truncation():
    denomination, reason = _validated("PKR\n10000")
    assert denomination is None and reason, (denomination, reason)


@check("currency: every circulating PKR note is accepted")
def t_pkr_denominations():
    # 5, 75 (2022 commemorative) and 200 were missing, so those notes were
    # announced as unrecognised even when read correctly.
    for expected in ("5", "10", "20", "50", "75", "100", "200", "500", "1000", "5000"):
        assert expected in VALID_DENOMINATIONS, expected
        assert _validated(f"PKR\n{expected}") == (expected, None), expected
    for bogus in ("300", "1500", "25", "0"):
        assert bogus not in VALID_DENOMINATIONS, bogus


# ---------------------------------------------------------------- medicine ---

DATE_CASES = [
    ("2027-03-31", date(2027, 3, 31)),
    ("2027/03/31", date(2027, 3, 31)),
    ("2027.03.31", date(2027, 3, 31)),
    ("31-12-2027", date(2027, 12, 31)),
    ("31/12/2027", date(2027, 12, 31)),
    ("03/2027", date(2027, 3, 31)),      # month-only = end of month
    ("2027-03", date(2027, 3, 31)),
    ("MAR 2027", date(2027, 3, 31)),
    ("March 2027", date(2027, 3, 31)),
    ("15 Mar 2027", date(2027, 3, 15)),
    ("15-MAR-2027", date(2027, 3, 15)),
    ("March 15, 2027", date(2027, 3, 15)),
    ("02/2028", date(2028, 2, 29)),      # leap year end-of-month
]

# Bare digit runs must stay unparseable: this is the guard that keeps a batch or
# lot number from being announced as an expiry date.
NON_DATES = [
    "",
    "20270331",
    "1234567",
    "500",
    "B-1234",
    "EXPIRY_NOT_VISIBLE",
    "MFG_NOT_VISIBLE",
    "NO_DATES",
    "500mg",
    "10mg/5ml",
    "Unclear",
    "25/25/2027",
]


@check("medicine: dates are read in the formats packs actually print")
def t_dates_accepted():
    for text, expected in DATE_CASES:
        assert parse_date(text) == expected, f"{text!r} -> {parse_date(text)!r}"


@check("medicine: bare numbers and sentinels are never dates")
def t_non_dates():
    for text in NON_DATES:
        assert parse_date(text) is None, f"{text!r} parsed as {parse_date(text)!r}"


@check("medicine: a bare year is trusted only when labelled as the expiry")
def t_bare_year():
    assert parse_date("2027") is None
    assert parse_date("2027", allow_bare_year=True) == date(2027, 12, 31)
    assert parse_date("1234", allow_bare_year=True) is None


@check("medicine: a manufacturing date is never mistaken for the expiry")
def t_mfg_is_not_expiry():
    # The dangerous failure: reporting a safe medicine as "Khatra! Expired".
    for line in ("MFG: 2024-01-15", "Mfg 2024-01-15", "Manufacturing Date: 2024-01-15"):
        parsed, kind = read_date_line(line)
        assert kind == "mfg", (line, kind)
        assert parsed == date(2024, 1, 15), (line, parsed)
        expiry, mfg = read_dates([line])
        assert expiry is None, (line, expiry)
        assert mfg == date(2024, 1, 15), (line, mfg)


@check("medicine: labelled expiry lines are read as the expiry")
def t_labelled_expiry():
    for line in (
        "EXP: 2027-03-31",
        "Expiry Date: 2027-03-31",
        "Expiry: 03/2027",
        "Use Before 31-12-2027",
        "Best Before: DEC 2027",
        "EXP 2027",
    ):
        parsed, kind = read_date_line(line)
        assert kind == "expiry", (line, kind)
        assert parsed is not None, line
        assert read_dates([line])[0] == parsed, line


@check("medicine: batch and lot values are ignored entirely")
def t_batch_ignored():
    for line in ("Batch: 2024-01-15", "Lot 45452", "B.No: 20270101", "Ref: 12-2027"):
        parsed, kind = read_date_line(line)
        assert parsed is None, (line, parsed)
        assert kind == "batch", (line, kind)
        assert read_dates([line], second_is_mfg=True) == (None, None), line


@check("medicine: the documented two-line shapes still work")
def t_documented_shapes():
    # Step 1: name line is stripped by the caller, so only date lines arrive.
    assert read_dates(["2027-03-31"]) == (date(2027, 3, 31), None)
    assert read_dates(["EXPIRY_NOT_VISIBLE"]) == (None, None)
    # Label scan: bare expiry first, bare manufacturing date second.
    assert read_dates(["2027-03-31", "2024-01-15"], second_is_mfg=True) == (
        date(2027, 3, 31),
        date(2024, 1, 15),
    )
    assert read_dates(["EXPIRY_NOT_VISIBLE", "2024-01-15"], second_is_mfg=True) == (
        None,
        date(2024, 1, 15),
    )
    # Without the label-scan flag a second unlabeled date is not guessed at.
    assert read_dates(["2027-03-31", "2024-01-15"]) == (date(2027, 3, 31), None)


@check("medicine: an unlabeled date in a later line is not adopted as the expiry")
def t_position_matters():
    expiry, mfg = read_dates(["EXPIRY_NOT_VISIBLE", "2027-03-31"])
    assert expiry is None, expiry
    assert mfg is None, mfg


@check("medicine: expiry in the past is 'expired', future is 'safe'")
def t_status():
    assert medicine_status(None) == "unknown"
    assert medicine_status(date(2000, 1, 1)) == "expired"
    assert medicine_status(date(2999, 1, 1)) == "safe"
    assert medicine_status(date.today()) == "safe"


EXTRA_NAMES = [
    ("Name: Panadol 500mg", "Panadol 500mg"),
    ("Line 1: Panadol 500mg", "Panadol 500mg"),
    ("Medicine: Co-Amoxiclav 625mg", "Co-Amoxiclav 625mg"),
    ("Ventolin Inhaler 100mcg", "Ventolin Inhaler 100mcg"),
    ("Insulatard 100IU/ml", "Insulatard 100IU/ml"),
    ("Vitamin B12 1000mcg", "Vitamin B12 1000mcg"),
    ("ORS Sachet", "ORS Sachet"),
    ("Pantoprazole 40mg + Domperidone 30mg SR Capsules",
     "Pantoprazole 40mg + Domperidone 30mg SR Capsules"),
    ("بروفین 400mg", "بروفین 400mg"),
]


@check("medicine: label prefixes are stripped, real names survive")
def t_name_prefixes():
    for raw, expected in EXTRA_NAMES:
        assert clean_name(raw) == expected, f"{raw!r} -> {clean_name(raw)!r}"


@check("medicine: long multi-salt names are no longer rejected by the cap")
def t_long_name_cap():
    long_name = (
        "Amoxicillin Trihydrate and Potassium Clavulanate Diluted "
        "Dispersible Tablets 400mg + 57mg"
    )
    assert len(long_name) > 80, len(long_name)
    assert len(long_name) <= 120, len(long_name)
    assert clean_name(long_name) == long_name


@check("medicine: voice_name speaks the name in Urdu script and spells out plus")
def t_voice_name():
    assert voice_name("Getformin 2mg + 500mg") == "گیتفورمین 2mg plus 500mg"
    assert voice_name("Panadol 500mg") == "پانادول 500mg"
    assert voice_name("Panadol 500 milligrams") == "پانادول 500 mg"


# -------------------------------------------------------------------- db ---

def _temp_db():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    os.unlink(handle.name)  # let init_db create it
    return handle.name


@check("db: a scan round-trips with expiry and manufacturing dates")
def t_db_roundtrip():
    saved = db.DB_PATH
    db.DB_PATH = _temp_db()
    try:
        db.init_db()
        item_id = db.add_item(
            "medicine", "Panadol 500mg", "safe", "2027-03-31", "2024-01-15"
        )
        assert isinstance(item_id, int) and item_id > 0, item_id
        row = db.get_item(item_id)
        assert row["name"] == "Panadol 500mg", row
        assert row["expiry_date"] == "2027-03-31", row
        assert row["mfg_date"] == "2024-01-15", row
        assert row["status"] == "safe", row
        assert row["timestamp"], row

        currency_id = db.add_item("currency", "Rs. 500", "success")
        assert db.get_item(currency_id)["mfg_date"] is None

        items = db.get_items()
        assert len(items) == 2, items
        assert items[0]["id"] == currency_id, "newest scan must come first"

        assert db.delete_item(currency_id) is True
        assert db.delete_item(currency_id) is False
        assert len(db.get_items()) == 1
    finally:
        db.DB_PATH = saved


@check("db: a label scan cannot erase a stored expiry with None")
def t_db_partial_update():
    saved = db.DB_PATH
    db.DB_PATH = _temp_db()
    try:
        db.init_db()
        item_id = db.add_item("medicine", "Brufen 400mg", "safe", "2027-06-30", None)
        db.update_item(item_id, expiry_date=None, mfg_date="2024-02-01", status=None)
        row = db.get_item(item_id)
        assert row["expiry_date"] == "2027-06-30", row
        assert row["status"] == "safe", row
        assert row["mfg_date"] == "2024-02-01", row

        db.update_item(item_id, expiry_date="2020-01-01", status="expired")
        row = db.get_item(item_id)
        assert row["expiry_date"] == "2020-01-01", row
        assert row["status"] == "expired", row
        assert db.update_item(item_id) == 0
    finally:
        db.DB_PATH = saved


@check("db: update_item refuses any column not on the whitelist")
def t_db_column_whitelist():
    saved = db.DB_PATH
    db.DB_PATH = _temp_db()
    try:
        db.init_db()
        item_id = db.add_item("medicine", "Panadol 500mg", "safe")
        for attack in (
            {"id": 999},
            {"timestamp": "1999-01-01"},
            {"status = 'x' WHERE 1=1; --": "y"},
            {"expiry_date": "2027-01-01", "type": "currency"},
        ):
            try:
                db.update_item(item_id, **attack)
            except ValueError:
                continue
            raise AssertionError(f"accepted a non-whitelisted update: {attack!r}")
        assert db.get_item(item_id)["status"] == "safe"
        assert db.get_item(item_id)["type"] == "medicine"
    finally:
        db.DB_PATH = saved


EXPECTED_COLUMNS = {
    "id",
    "type",
    "name",
    "status",
    "expiry_date",
    "mfg_date",
    "timestamp",
}


@check("db: an old table gains mfg_date without losing its rows")
def t_db_migration():
    saved = db.DB_PATH
    path = _temp_db()
    db.DB_PATH = path
    try:
        # Recreate the pre-change schema with a row already in it.
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE scanned_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                expiry_date TEXT,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO scanned_items (type, name, status, expiry_date, timestamp) "
            "VALUES ('medicine', 'Old Row 500mg', 'safe', '2027-01-01', '2026-01-01 10:00:00')"
        )
        conn.commit()
        conn.close()

        db.init_db()  # must add the column, not rebuild or drop anything
        rows = db.get_items()
        assert len(rows) == 1, rows
        assert rows[0]["name"] == "Old Row 500mg", rows
        assert rows[0]["expiry_date"] == "2027-01-01", rows
        assert rows[0]["mfg_date"] is None, rows

        new_id = db.add_item("medicine", "New Row 250mg", "unknown", None, "2024-05-05")
        assert db.get_item(new_id)["mfg_date"] == "2024-05-05"
        assert len(db.get_items()) == 2
    finally:
        db.DB_PATH = saved


@check("db: a stored row carries nothing but what a real scan observed")
def t_db_schema():
    """Pinned to an allowlist rather than a denylist: Meri List is exactly what
    the model read, so a column that could label a row as standing for something
    else has to be added deliberately, and this fails until the author looks."""
    saved = db.DB_PATH
    db.DB_PATH = _temp_db()
    try:
        db.init_db()
        conn = sqlite3.connect(db.DB_PATH)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(scanned_items)")}
        conn.close()
        assert columns == EXPECTED_COLUMNS, columns

        item_id = db.add_item("currency", "Rs. 500", "success")
        assert set(db.get_item(item_id)) == EXPECTED_COLUMNS, db.get_item(item_id)
        assert set(db.get_items()[0]) == EXPECTED_COLUMNS, db.get_items()

        try:
            db.add_item("currency", "Rs. 500", "success", synthetic=True)
        except TypeError:
            pass
        else:
            raise AssertionError("add_item accepted a field a real scan cannot produce")
        assert len(db.get_items()) == 1
    finally:
        db.DB_PATH = saved


@check("db: a connection failure cannot leave the handle open")
def t_db_connection_cleanup():
    saved = db.DB_PATH
    db.DB_PATH = _temp_db()
    try:
        db.init_db()
        item_id = db.add_item("medicine", "Panadol 500mg", "safe")
        try:
            db.update_item(item_id, **{"status = 'x' WHERE 1=1; --": "y"})
        except ValueError:
            pass
        # Still usable afterwards, and the value is untouched.
        assert db.get_item(item_id)["status"] == "safe"
        assert db.add_item("currency", "Rs. 100", "success") > item_id
    finally:
        db.DB_PATH = saved


@check("db: the database follows the Railway volume and survives a restart")
def t_db_volume_persistence():
    volume_dir = tempfile.mkdtemp(prefix="nigah_volume_")
    expected_db = os.path.join(volume_dir, "nigah.db")

    def run(code, mount):
        # A fresh interpreter is what a Railway restart looks like to the app:
        # db.py is imported again, this time against the mounted volume.
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            env={**os.environ, "RAILWAY_VOLUME_MOUNT_PATH": mount},
            capture_output=True,
            text=True,
        )

    write = run(
        "import os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import db\n"
        f"assert db.DB_PATH == {expected_db!r}, db.DB_PATH\n"
        "db.init_db()\n"
        "db.add_item('medicine', 'Panadol 500mg', 'safe')\n",
        volume_dir,
    )
    assert write.returncode == 0, write.stderr
    assert os.path.exists(expected_db), expected_db

    restart = run(
        "import os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import db\n"
        f"assert db.DB_PATH == {expected_db!r}, db.DB_PATH\n"
        "items = db.get_items()\n"
        "assert len(items) == 1, items\n"
        "assert items[0]['name'] == 'Panadol 500mg', items\n",
        volume_dir,
    )
    assert restart.returncode == 0, restart.stderr

    local = run(
        "import os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import db\n"
        "assert os.path.dirname(db.DB_PATH) == os.getcwd(), db.DB_PATH\n",
        "",
    )
    assert local.returncode == 0, local.stderr


def main():
    failures = []
    for name, fn in CHECKS:
        try:
            fn()
        except Exception as error:
            failures.append(name)
            print(f"FAIL  {name}\n      {type(error).__name__}: {error}")
        else:
            print(f"PASS  {name}")
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
