import os

from flask import Flask, send_from_directory
from flask_cors import CORS

import db
import gemini_client
from routes.currency import currency_bp
from routes.medicine import medicine_bp
from routes.merilist import merilist_bp
from routes.speech import speech_bp

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
CORS(app)
app.register_blueprint(currency_bp)
app.register_blueprint(medicine_bp)
app.register_blueprint(merilist_bp)
app.register_blueprint(speech_bp)

try:
    db.init_db()
except Exception as error:
    print(f"[startup] database initialization failed: {error}", flush=True)


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/health")
def health():
    # Reports the one vision model this process will call, so a deployed
    # instance can be verified from outside.
    return f"Nigah AI Backend Running ({gemini_client.health_summary()})"


@app.route("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    # Production runs gunicorn (Procfile / railway.json), so this only affects a
    # local `python app.py`: APP_MODE=production turns the debug reloader off.
    port = int(os.environ.get("PORT", 5000))
    debug = (os.getenv("APP_MODE") or "development").strip().lower() != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
