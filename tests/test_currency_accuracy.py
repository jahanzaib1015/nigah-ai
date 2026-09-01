import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "http://127.0.0.1:5000/detect-currency"
FOLDER = Path(__file__).resolve().parent.parent / "currency_PKR"
PACING_SECONDS = 3.5
MAX_RETRIES = 4


def detect(path):
    boundary = "----nigahAccuracyBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")

    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (429, 500) and attempt < MAX_RETRIES - 1:
                wait = 30 * (attempt + 1)
                print(f"    quota/busy ({error.code}), waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            return {"success": False, "error": f"HTTP {error.code}"}
        except Exception as error:  # network-level failure
            return {"success": False, "error": str(error)}
    return {"success": False, "error": "rate-limited"}


def main():
    files = sorted(
        FOLDER.glob("note_*"),
        key=lambda p: (int(re.match(r"note_(\d+)", p.name).group(1)), p.name),
    )
    if not files:
        print("No note_* images found in currency_PKR/")
        return

    rows = []
    print(f"Testing {len(files)} images against {ENDPOINT}\n", flush=True)
    for index, path in enumerate(files, 1):
        match = re.match(r"note_(\d+)_", path.name)
        expected = match.group(1) if match else "?"
        result = detect(path)
        if result.get("success"):
            actual = str(result.get("denomination"))
        else:
            actual = f"ERROR: {result.get('error')}"
        passed = result.get("success") and actual == expected
        rows.append((path.name, expected, actual, passed))
        print(
            f"[{index:2d}/{len(files)}] {path.name:24s} expected={expected:5s} "
            f"actual={actual:5s} {'PASS' if passed else 'FAIL'}",
            flush=True,
        )
        time.sleep(PACING_SECONDS)

    passed_rows = [r for r in rows if r[3]]
    failed_rows = [r for r in rows if not r[3]]
    accuracy = 100.0 * len(passed_rows) / len(rows)

    print("\n" + "=" * 74)
    print(f"{'FILENAME':24s} | {'EXPECTED':8s} | {'ACTUAL':8s} | RESULT")
    print("-" * 74)
    for name, expected, actual, passed in rows:
        print(f"{name:24s} | {expected:8s} | {actual:8s} | {'PASS' if passed else 'FAIL'}")
    print("=" * 74)
    print(f"Total images : {len(rows)}")
    print(f"Passed       : {len(passed_rows)}")
    print(f"Failed       : {len(failed_rows)}")
    print(f"Accuracy     : {accuracy:.1f}%")

    if failed_rows:
        print("\nFailed images:")
        for name, expected, actual, _ in failed_rows:
            print(f"  - {name}: expected {expected}, got {actual}")
        print("\nFailures by denomination:")
        by_denom = {}
        for name, expected, actual, _ in failed_rows:
            by_denom.setdefault(expected, []).append(name)
        for denom in sorted(by_denom, key=int):
            print(f"  {denom:>5s} rupees: {len(by_denom[denom])} failed -> {', '.join(by_denom[denom])}")


if __name__ == "__main__":
    main()
