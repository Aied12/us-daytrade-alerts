#!/usr/bin/env python3
"""Small HTTP API for Web Push subscribe + VAPID public key."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.web_push import ensure_vapid_keys, remove_subscription, save_subscription  # noqa: E402

app = Flask(__name__)


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    return resp


@app.route("/api/push/vapid-public-key", methods=["GET"])
def vapid_public():
    keys = ensure_vapid_keys()
    return jsonify({"publicKey": keys["publicKey"]})


@app.route("/api/push/subscribe", methods=["POST", "OPTIONS"])
def subscribe():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(silent=True) or {}
    if not data.get("endpoint") or not (data.get("keys") or {}).get("p256dh"):
        return jsonify({"ok": False, "error": "bad subscription"}), 400
    save_subscription(data)
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST", "DELETE", "OPTIONS"])
def unsubscribe():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get("endpoint") or "")
    if endpoint:
        remove_subscription(endpoint)
    return jsonify({"ok": True})


@app.route("/api/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True})


def main() -> None:
    ensure_vapid_keys()
    app.run(host="0.0.0.0", port=int(__import__("os").getenv("PUSH_API_PORT", "5055")), debug=False)


if __name__ == "__main__":
    main()
