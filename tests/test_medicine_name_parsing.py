"""Unit tests for the medicine name parsing guards in routes/medicine.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from routes.medicine import (
    STRENGTH_ONLY_RE,
    _is_strength_only,
    clean_name,
    voice_name,
)

VALID = {
    "Panadol": "Panadol",
    "Panadol 500mg": "Panadol 500mg",
    "Getformin 2mg + 500mg": "Getformin 2mg + 500mg",
    "500mg Panadol": "Panadol 500mg",
    "10mg + 1000mg Getformin": "Getformin 10mg + 1000mg",
    "125mg/5ml Calpol": "Calpol 125mg/5ml",
    "Co-Amoxiclav 625mg": "Co-Amoxiclav 625mg",
    "Paracetamol Compound Extra Strength Film Coated Tablets 500mg": (
        "Paracetamol Compound Extra Strength Film Coated Tablets 500mg"
    ),
    "  'Panadol 500mg'  ": "Panadol 500mg",
    "پانڈول 500mg": "پانڈول 500mg",
    "پانڈول 500": "پانڈول 500",
    "Amoxicillin 250mg": "Amoxicillin 250mg",
    # Near misses for the refusal guard: a real brand may start with "No"/"Not".
    "Novalgin 500mg": "Novalgin 500mg",
    "Notofen 400mg": "Notofen 400mg",
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
    "0.5 g + 250 mg",
    "125mg/5ml",
    "10ml/5ml",
    "500 milligram",
    "250 milligrams",
    "500",
    "500 + 250",
    "500/250",
    "+",
    "UNKNOWN",
    "EXPIRY_NOT_VISIBLE",
    "None",
    "N/A",
    "x" * 121,
    # Prose refusals: made of letters, so only the refusal guard catches them.
    # Saved as a name these would be spoken as "yeh <apology> dawai hai".
    "sorry, the packaging text is cut off",
    "I cannot determine the brand name",
    "Unable to read the name",
    "The brand name is not visible",
    "No medicine name visible",
    "UNCLEAR",
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
    assert STRENGTH_ONLY_RE.match("125mg/5ml")
    assert not STRENGTH_ONLY_RE.match("Panadol 500mg")
    assert _is_strength_only("500")
    assert _is_strength_only("500mg")
    assert _is_strength_only("500 + 250")
    assert _is_strength_only("125mg/5ml")
    assert not _is_strength_only("Panadol")
    assert not _is_strength_only("پانڈول")
    assert voice_name("Getformin 2mg + 500mg") == "گیتفورمین 2mg plus 500mg"
    assert voice_name("Panadol 500 milligram") == "پانادول 500 mg"
    assert clean_name("x" * 120) is not None, "a 120-char name must survive"
    assert clean_name("x" * 121) is None, "the cap must still reject a paragraph"
    print("guard + voice_name OK")


if __name__ == "__main__":
    main()
