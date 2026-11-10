# language: python, file: app.py, target: Waifly / Render (Python 3)
import os
import io
import base64
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TG_API = "https://api.telegram.org/bot" + TELEGRAM_TOKEN

@app.route("/", methods=["GET"])
def health():
    return "auto-feedback server is alive", 200

@app.route("/feedback", methods=["POST"])
def feedback():
    raw_b64 = request.form.get("base64_image", "")
    caption = request.form.get("caption", "PUBG auto feedback")

    if not raw_b64:
        return jsonify({"ok": False, "error": "no image"}), 400

    try:
        image_bytes = base64.b64decode(raw_b64)
    except Exception as e:
        return jsonify({"ok": False, "error": "b64 decode: " + str(e)}), 400

    try:
        resp = requests.post(
            TG_API + "/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
            files={"photo": ("screenshot.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            timeout=60,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "telegram: " + str(e)}), 502

    return jsonify({"ok": resp.ok, "telegram": resp.text}), (200 if resp.ok else 502)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
