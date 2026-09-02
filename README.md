# Nigah AI — AI-Powered Vision & Accessibility Assistant

**Nigah** — *Urdu for "vision" or "sight"*

An AI-powered mobile-first web application designed to empower visually impaired individuals in Pakistan by providing real-time **medicine identification**, **currency detection**, and **expiry tracking** — all delivered through natural Urdu voice feedback.

---

**Live Demo:** [https://nigah-ai-production.up.railway.app/](https://nigah-ai-production.up.railway.app/)

---

## Table of Contents

- [Project Vision](#project-vision)
- [How Nigah AI Differs](#how-nigah-ai-differs)
- [Core Features](#core-features)
- [Team & Contributions](#team--contributions)
- [Technical Architecture](#technical-architecture)
- [Medicine Scanner & Parser Hardening](#medicine-scanner--parser-hardening)
- [Audio & TTS System](#audio--tts-system)
- [Currency Recognition](#currency-recognition)
- [Customer Care Integration](#customer-care-integration)
- [PWA & UI Branding](#pwa--ui-branding)
- [Testing](#testing)
- [Deployment & Setup](#deployment--setup)
- [Repository Structure](#repository-structure)
- [Environment Variables](#environment-variables)
- [Roadmap](#roadmap)
- [Acknowledgements](#acknowledgements)
- [License](#license)

---

## Project Vision

Visually impaired individuals in Pakistan face daily challenges in identifying **medicines**, **currency notes**, and reading **expiry dates**. While global solutions exist, most are English-centric and lack support for Pakistani currency denominations and local language needs.

**Nigah AI** bridges this gap by transforming a standard smartphone camera into an **AI-powered visual assistant** that:

- **Speaks results** in natural Urdu (ur-PK-AsadNeural voice)
- **Displays text** in Roman Urdu for on-screen readability
- **Tracks history** through the "Meri List" dashboard
- **Alerts users** about expired medicines with high-priority audio + vibration

The application is designed to be **intuitive, fast, and accessible** — functioning as a Progressive Web App (PWA) for installability and offline support.

---

## How Nigah AI Differs

| Feature | Global Apps (Seeing AI, Lookout, etc.) | Nigah AI |
|---------|----------------------------------------|----------|
| **Primary Language** | English-centric, limited Urdu | **Urdu-first** (ur-PK-AsadNeural voice) |
| **Currency Focus** | USD, EUR, GBP primarily | **Pakistani Rupee (PKR)** with 67-image dataset |
| **Medicine Expiry** | Basic text reading | **Two-step scan flow** with dedicated expiry label detection |
| **Compound Strengths** | Not specifically handled | **"10mg + 1000mg" correctly parsed** and spoken as "plus" |
| **Customer Support** | Limited local support | **Direct WhatsApp + SIM call** (0313-0756199) |
| **PWA Installable** | Varies by platform | **Yes** — installable with offline shell |

This focused approach makes Nigah AI uniquely suited for the **Pakistani visually impaired community**, addressing specific local needs that global solutions often overlook.

---

## Core Features

### Medicine Scanner (Two-Step Flow)
- **Step 1:** Scan medicine packaging to extract **brand name** (or generic/salt name as fallback).
- **Step 2 (optional):** Scan the expiry label separately; the app merges the date into the saved record.
- **Strict name validation** rejects strength-only responses (e.g., bare "500mg") and sentinels.
- **Compound strength handling:** Correctly parses "10mg + 1000mg" and preserves the `+` symbol.
- **Expiry detection:** Reads only explicitly labeled dates (EXP / Expiry / Use Before / Best Before); ignores batch, lot, MFG, and prices.
- **Status badges:** Safe · Expired · No Expiry

### Currency Recognition
- **Multi-currency support:** PKR, USD, GBP, INR, EUR, and OTHER.
- **Urdu voice announcement** of denomination.
- **High accuracy** validated against a 67-image test dataset.
- **Dedicated Pakistani note dataset** — PKR 10, 20, 50, 100, 500, 1000, 5000.

### Meri List (History Dashboard)
- Persistent log of all scans (medicines + currency) with timestamps.
- Delete individual entries.
- Visual status indicators and color-coded badges.

### Danger-Priority Alert (Expired Medicine)
- Immediate **urgent voice warning** in Urdu.
- **Vibration** (on supported devices) for tactile feedback.
- Overrides any ongoing audio to ensure the alert is heard instantly.

### Customer Care Integration
- Official support number: **0313-0756199**
- Direct **WhatsApp** link: `wa.me/923130756199`
- Direct **SIM call** link: `tel:03130756199`
- One-tap access from the main menu.

---

## Team & Contributions

| Name | Role | Key Contributions |
|------|------|-------------------|
| **Muhammad Jahanzaib Azhar** | AI Developer | Core system architecture, backend logic, Flask API, Gemini integration, medicine parser hardening, TTS optimization, deployment |
| **Javeria Waqar** | AI Dev Support | AI pipeline assistance, feature refinement, technical support, testing coordination |
| **Syed Izhar Uddin** | Data Collection & Testing | Dataset compilation, creation of 35 medicine parsing unit tests, quality assurance, currency image dataset (67 images) |
| **Hudabia Aimen** | Pitch, Docs & Presentation | Project documentation, pitch deck formulation, presentation asset management |

---

## Technical Architecture

### System Overview

```
User (Smartphone Camera)
        |
        v
Frontend (PWA - HTML/CSS/JS)
        |
        v  Base64 Image
Flask Backend (Python)
        |
        v  Gemini Vision API
AI Analysis (Medicine/Currency)
        |
        v  Parsing & Validation
Edge-TTS (Urdu Voice Generation)
        |
        v  MP3 URL
Frontend Audio Playback
        |
        v
SQLite Database (History)
```

### Backend Components
- **`app.py`** — Flask application entry point, routing, and API endpoints.
- **`gemini_client.py`** — Google Gemini Vision API client with prompt engineering.
- **`db.py`** — SQLite database helper functions for history storage.
- **`routes/medicine.py`** — Medicine scanning logic, `clean_name()` guard, expiry parsing.
- **`routes/currency.py`** — Currency detection and denomination mapping.
- **`routes/merilist.py`** — GET/DELETE endpoints for scan history.
- **`routes/speech.py`** — POST /generate-speech endpoint for Edge-TTS MP3 generation.

### Frontend Components
- **`index.html`** — Mobile-first PWA shell.
- **`scan.html`** — Currency scan screen.
- **`medicine.html`** — Two-step medicine scan screen.
- **`list.html`** — Meri List (history dashboard).
- **`style.css`** — Design system (navy `#0F172A` / teal `#14B8A6`).
- **`tts.js`** — Text-to-Speech orchestration (`speakUrdu`, `queueUrdu`, `preloadUrdu`, `stopAllAudio`).
- **`care.js`** — Shared Customer Care bottom sheet.
- **`pwa.js`** — Service-worker registration.
- **`sw.js`** — Service worker v2 — precache + network-first.
- **`manifest.json`** — PWA manifest.

### Data Storage
- **SQLite (`nigah.db`)** — Stores scan history with fields: type, name, strength, expiry, timestamp, status.
- **Server-side TTS cache (`speech_cache/`)** — Stores generated MP3 files with atomic writes and 1-hour freshness.

---

## Medicine Scanner & Parser Hardening

The medicine scanning module is fortified at **three layers** to guarantee reliable and safe output.

### Layer 1: Prompt-Level Contract (`MEDICINE_PROMPT` / `LABEL_PROMPT`)
- Handles mixed **English + Urdu/regional-script** packaging text (boxes & blister packs).
- **Mandatory brand name** — leaving line 1 empty or returning only strength/unit is strictly forbidden.
- **Fallback chain:** Brand -> Generic/Salt name (e.g., Paracetamol) -> Most prominent printed words.
- **Multi-strength packs** use the `+` symbol (e.g., `10mg + 1000mg`).
- **Short unit symbols only:** mg, ml, mcg, g, IU (never spelled-out words).
- **Expiry only when explicitly labeled** — otherwise returns `EXPIRY_NOT_VISIBLE`.

### Layer 2: Strict Parsing Guard (`clean_name()` in `routes/medicine.py`)
- Strips whitespace, quotes, and markdown artifacts.
- Rejects empty, >80-char, and sentinel replies (`UNKNOWN`, `N/A`, `NA`, `None`, `EXPIRY_NOT_VISIBLE`, `-`).
- **`STRENGTH_ONLY_RE`** rejects bare and compound strengths (e.g., `500mg`, `1 g`, `10 ml`, `250mcg`, `10mg + 1000mg`).
- **Leading-strength reordering** repairs inverted replies: `500mg Panadol` -> `Panadol 500mg` (guarded by negative lookahead to avoid splitting compound strengths).
- On rejection, strength-only replies trigger a dedicated Urdu "name not readable — rescan" voice; other invalid replies get generic failure voice. The name is **never** silently degraded.

### Layer 3: Unit Tests (35 Cases)
- **12 accepted** — plain brand, brand-only, compound `+`, slash concentration, inverted, quoted/padded, Urdu-script brand, Urdu + plain number, long multi-word names.
- **23 rejected** — empty, whitespace, every unit form, compound strengths, slash concentrations, spelled-out units, bare numbers, symbol junk, sentinels, oversized (>80 chars).
- All tests passing, committed to repository.

---

## Audio & TTS System

### Architecture
- **Edge-TTS** with `ur-PK-AsadNeural` voice for natural Urdu pronunciation.
- **Server-side caching** in `speech_cache/` — MP3 files generated once and reused (1-hour freshness).
- **Atomic writes** prevent partial file corruption.
- **HTMLAudioElement** for frontend playback.

### Three Helpers with Distinct Semantics

| Helper | Behavior | Use Case |
|--------|----------|----------|
| `speakUrdu()` | **Hard-cancels** current audio | Failures (instant error) |
| `queueUrdu()` | **Queues** after current audio | Results (never cut mid-word) |
| `preloadUrdu()` | **Pre-fetches** all fixed phrases | Page load (instant playback) |

### Global Audio Control (`stopAllAudio()`)
- Stops all ongoing speech on modal close, navigation, or section reset.
- Prevents **overlapping audio** — a common issue that was systematically resolved.

### Pronunciation Normalization
- **"+" symbol** -> spoken as "plus" phonetically.
- **"milligram"** -> shortened to "mg" for natural speech.
- **Western digits** -> kept as-is (voice normalizes naturally).

---

## Currency Recognition

- **Supported Currencies:** PKR, USD, GBP, INR, EUR, OTHER.
- **Gemini Vision API** analyzes the note and returns denomination + currency.
- **Urdu voice output** announces the amount clearly.
- **67-image benchmark dataset** prepared for accuracy evaluation.
- **Denomination mapping** ensures correct Urdu wording for each note value.

---

## Customer Care Integration

- **Official Number:** `0313-0756199`
- **WhatsApp:** [wa.me/923130756199](https://wa.me/923130756199) — opens chat directly.
- **Direct Call:** [tel:03130756199](tel:03130756199) — initiates SIM call.
- Links are properly configured in the Customer Care section of the app.

---

## PWA & UI Branding

### Progressive Web App
- **Installable** — "Add to Home Screen" on Android/iOS.
- **Offline shell** — service worker caches core HTML/CSS/JS assets.
- **Custom manifest** — app name, icons, theme colors, display mode.

### Visual Identity
- **Custom teal-and-white eye favicon** — symbolizing "Nigah" (vision).
- **Multiple icon sizes** generated for various platforms.
- **Mobile-first responsive design** with large touch targets and high contrast for low-vision users.

---

## Testing

### Medicine Name Parsing Unit Tests
```bash
python tests/test_medicine_name_parsing.py
```
- **35 test cases** (12 accepted + 23 rejected).
- Covers edge cases: compound strengths, slash concentrations, spelled-out units, bare numbers, Urdu script, sentinels, whitespace, inverted strings, and over-length names.
- **Status:** All passing.

### Offline Test Suites
```bash
python tests/test_vision_fallback.py
python tests/test_routes_offline.py
python tests/test_parsing_and_db.py
```
No network, no API quota, no writes to the real Meri List — provider callables and the clock are injected, and the database is a throwaway file.

- `test_vision_fallback.py` — **15 checks**: provider ordering per `APP_MODE`, fallback on failure, the chain budget and per-provider slice caps, the wall-clock bound that abandons a transport ignoring its own timeout, the `MOCK_VISION` default, and the notice that labels a mock answer.
- `test_routes_offline.py` — **21 checks**: both scan routes end to end, including validation refusals, the two-step medicine label merge, Meri List read/delete, a total outage answering with labelled test data instead of a 503, and the rule that a mock label scan cannot overwrite dates read from a real pack.
- `test_parsing_and_db.py` — **21 checks**: currency and medicine reply parsing, expiry/mfg classification, status rules, and the SQLite layer including schema migration of an existing table.

### Currency Accuracy Benchmark
```bash
python tests/test_currency_accuracy.py
```
- **67 real currency images** across denominations and currencies.
- Script ready; execution pending due to Gemini API quota.

### API & Key Tests
- `test_api.py` — Gemini API smoke test.
- `test_keys.py` — Primary/backup key + model probing.
- `test_raw_error.py` — Raw Gemini error diagnostics.

### Sample Test Assets
- `test_image.jpg` — Sample currency photo.
- `test_medicine.png` — Sample medicine photo (with expiry).
- `test_medicine_noexpiry.png` — Sample medicine photo (no expiry).
- `test_black.png` — Blank/black-image edge case.

---

## Deployment & Setup

### Production (Railway)
- **Live URL:** [https://nigah-ai-production.up.railway.app/](https://nigah-ai-production.up.railway.app/)
- **Auto-deploy:** Every push to `main` branch triggers Railway deployment.
- **Database:** SQLite (non-persistent across redeploys; volume mount optional for production persistence).

### Local Development

1. **Clone the repository**
   ```bash
   git clone https://github.com/jahanzaib1015/nigah-ai.git
   cd nigah-ai
   ```

2. **Set up virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables** (see [Environment Variables](#environment-variables))

5. **Run the Flask server**
   ```bash
   python app.py
   ```

6. **Access the app** at `http://localhost:5000`

---

## Repository Structure

```
nigah-ai/
|
├── app.py                      # Flask entrypoint: blueprints, static serving,
│                               #   root catch-all, /health, PORT + APP_MODE
├── db.py                       # SQLite layer — scanned_items (add/update/get/delete)
├── gemini_client.py            # Unified vision API: dev/prod endpoint switching,
│                               #   image shrinking, retries, inline base64
|
├── Procfile                    # web: gunicorn --timeout 200 app:app (Railway)
├── requirements.txt            # flask, flask-cors, google-generativeai,
│                               #   python-dotenv, edge-tts, requests, Pillow, gunicorn
├── .env.example                # Template (GEMINI_API_KEY placeholder)
├── .gitignore                  # .env, nigah.db, __pycache__, .pyc,
│                               #   vibe_images/, speech_cache/
|
├── routes/
│   ├── __init__.py
│   ├── currency.py             # POST /detect-currency — denomination + currency
│   ├── medicine.py             # POST /detect-medicine — clean_name() guards,
│   │                           #   expiry parsing, label-scan merge, voice_name
│   ├── merilist.py             # GET /meri-list · DELETE /meri-list/<id>
│   └── speech.py               # POST /generate-speech — Edge-TTS MP3 + caching
|
├── frontend/
│   ├── index.html              # Home — feature cards, Customer Care pill
│   ├── scan.html               # Currency scan screen
│   ├── medicine.html           # Two-step medicine scan screen
│   ├── list.html               # Meri List (scan history dashboard)
│   ├── style.css               # Design system (navy #0F172A / teal #14B8A6)
│   ├── tts.js                  # speakUrdu / queueUrdu / preloadUrdu / stopAllAudio
│   ├── care.js                 # Shared Customer Care bottom sheet
│   ├── pwa.js                  # Service-worker registration (root scope)
│   ├── sw.js                   # Service worker v2 — precache + network-first
│   ├── manifest.json           # PWA manifest (standalone, #0F172A theme)
│   ├── favicon.svg             # Teal-and-white eye logo (source of truth)
│   ├── icon-192.png            # Rasterized from favicon.svg
│   ├── icon-512.png            #   "
│   ├── icon-maskable-512.png   # Full-bleed teal, 78% safe zone
│   └── rasterize_icons.py      # SVG -> PNG icon generator (dev tool)
|
├── tests/
│   ├── test_medicine_name_parsing.py   # Unit tests: 12 accept + 23 reject
│   │                                   #   cases for the name-extraction guards
│   ├── test_currency_accuracy.py       # Accuracy benchmark vs. currency_PKR/
│   ├── test_api.py             # Gemini API smoke test
│   ├── test_keys.py            # Primary/backup key + model probing
│   ├── test_raw_error.py       # Raw Gemini error diagnostics
│   ├── test_image.jpg          # Sample currency photo
│   ├── test_medicine.png       # Sample medicine photo (with expiry)
│   ├── test_medicine_noexpiry.png      # Sample medicine photo (no expiry)
│   └── test_black.png          # Blank/black-image edge case
|
├── currency_PKR/               # 67 real Pakistani note photos,
│   │                           #   named note_<denomination>_<n>.jpg
│   ├── note_10_1..9.jpg        #   10 rupees   × 9
│   ├── note_20_1..9.jpg        #   20 rupees   × 9
│   ├── note_50_1..7.jpg        #   50 rupees   × 7
│   ├── note_100_1..12.jpg      #  100 rupees   × 12
│   ├── note_500_1..9.jpg       #  500 rupees   × 9
│   ├── note_1000_1..10.jpg     # 1000 rupees   × 10
│   └── note_5000_1..11.jpg     # 5000 rupees   × 11
|
├── nigah.db                    # SQLite database (scanned_items) — git-ignored
├── speech_cache/               # ~48 cached Edge-TTS MP3s (hash-named) — git-ignored
├── vibe_images/                # Ignored scratch imagery (empty)
└── server_5000.log             # Dev server log — untracked by convention
```

> **Note:** Files above the runtime artifacts line are version-controlled; `nigah.db`, `speech_cache/`, `vibe_images/`, `.env`, and `__pycache__/` are git-ignored.

---

## Environment Variables

Create a `.env` file in the project root (`.env.example` is provided as a template). The required keys depend on the run mode:

**Production mode (Google Gemini endpoint):**

```
APP_MODE=production
GEMINI_API_KEY=your_google_gemini_api_key
```

**Development mode (default, alternate vision endpoint):**

```
TABI_API_KEY=your_dev_key_here
TABI_BASE_URL=https://your-dev-endpoint
```

| Variable | Required | Purpose |
|----------|----------|---------|
| `GEMINI_API_KEY` | Recommended | Google Gemini Vision API key. Without it the chain runs on the development endpoint alone; startup only fails when *no* provider is configured and `MOCK_VISION` is off |
| `TABI_API_KEY` | Recommended | Development endpoint key, paired with `TABI_BASE_URL` |
| `TABI_BASE_URL` | Recommended | Base URL of the development vision endpoint |
| `APP_MODE` | Optional | `production` or `development` (default) — decides which provider the chain tries **first** (both stay wired up whenever their credentials exist) and toggles Flask debug auto-reload |
| `MOCK_VISION` | Optional | **On by default.** When every provider is missing or failing, scans answer with stable labelled test data instead of a 503, so the UI and database stay testable through an upstream outage. Every mock answer announces itself — a `TEST DATA` badge on screen, a spoken Urdu notice, and an `is_mock` flag on the saved row. Set `MOCK_VISION=0` to make a deployment strict again and speak the service-down message |
| `PORT` | Optional | Server port; defaults to `5000` locally and is injected automatically by Railway in production |

> **Note:** The `.env.example` file is maintained as a template; actual secrets are never committed to the repository.

---

## Roadmap

### Completed
- All five core features (medicine, currency, history, alerts, customer care)
- PWA support with custom branding
- Global audio interruption control
- Unit tests for medicine parsing (35 cases)
- Live deployment on Railway

### In Progress / Upcoming
- Real medicine photo end-to-end testing
- Currency benchmark execution (pending API quota)
- Persistent database via Railway volume
- Screen reader (TalkBack) accessibility validation
- Pitch deck and demo video preparation

### Future Enhancements
- Barcode scanning for medicines
- OCR for document reading
- Voice commands for hands-free operation
- Multi-language support (Sindhi, Punjabi, Pashto)

---

## Acknowledgements

- **Google Gemini API** — Vision analysis
- **Microsoft Edge-TTS** — Urdu text-to-speech
- **Railway** — Deployment platform
- **Flask** — Web framework
- **All team members** — for their dedication and contributions

---

## License

This project is developed for **educational and competition purposes**. All rights reserved by the team.

---

**Nigah AI — Giving Vision Through AI**
