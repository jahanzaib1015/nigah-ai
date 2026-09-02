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
    # Reports the resolved endpoint so a deployed instance can be verified from
    # outside: a silent fallback to the dev proxy is otherwise invisible.
    model = (
        gemini_client.GEMINI_MODEL
        if gemini_client.APP_MODE == "production"
        else gemini_client.TABI_MODEL
    )
    return f"Nigah AI Backend Running (mode={gemini_client.APP_MODE}, model={model})"


@app.route("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    # gemini_client resolves the real mode (it forces production on Railway),
    # so debug auto-reload must follow that verdict, not the raw env var.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=(gemini_client.APP_MODE != "production"))
