"""Unit tests for the medicine name parsing guards in routes/medicine.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from routes.medicine import STRENGTH_ONLY_RE, clean_name, voice_name

VALID = {
    "Panadol 500mg": "Panadol 500mg",
    "Getformin 2mg + 500mg": "Getformin 2mg + 500mg",
    "500mg Panadol": "Panadol 500mg",
    "10mg + 1000mg Getformin": "Getformin 10mg + 1000mg",
    "  'Panadol 500mg'  ": "Panadol 500mg",
    "پانڈول 500mg": "پانڈول 500mg",
    "Amoxicillin 250mg": "Amoxicillin 250mg",
}

REJECTED = [
    "",
    "   ",
    "500mg",
    "500 mg",
    "1 g",
    "10 ml",
    "250mcg",
    "10mg + 1000mg",
    "250mg + 500mg + 10mg",
    "UNKNOWN",
    "EXPIRY_NOT_VISIBLE",
    "None",
    "N/A",
    "x" * 61,
]


def main():
    for src, want in VALID.items():
        got = clean_name(src)
        assert got == want, f"clean_name({src!r}) = {got!r}, want {want!r}"
        print(f"accept  {src!r} -> {got!r}")

    for src in REJECTED:
        got = clean_name(src)
        assert got is None, f"clean_name({src!r}) = {got!r}, want None"
        print(f"reject  {src!r}")

    assert STRENGTH_ONLY_RE.match("500 mg")
    assert not STRENGTH_ONLY_RE.match("Panadol 500mg")
    assert voice_name("Getformin 2mg + 500mg") == "Getformin 2mg plus 500mg"
    assert voice_name("Panadol 500 milligram") == "Panadol 500 mg"
    print("guard + voice_name OK")


if __name__ == "__main__":
    main()
