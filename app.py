import os

from flask import Flask, send_from_directory
from flask_cors import CORS

import db
from routes.currency import currency_bp
from routes.medicine import medicine_bp
from routes.merilist import merilist_bp
from routes.speech import speech_bp

app = Flask(__name__)
CORS(app)
app.register_blueprint(currency_bp)
app.register_blueprint(medicine_bp)
app.register_blueprint(merilist_bp)
app.register_blueprint(speech_bp)

db.init_db()

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/health")
def health():
    return "Nigah AI Backend Running"


@app.route("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True)
