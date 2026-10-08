"""Web Push helpers: VAPID keys, subscription store, send urgent alerts."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SUBS_PATH = DATA / "push_subs.json"
VAPID_PATH = DATA / "vapid.json"
GATE_PATH = DATA / "push_gate.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, value: Any) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _vapid_usable(private_key: str) -> bool:
    """Reject corrupt/unsupported private keys that crash every cycle."""
    try:
        from py_vapid import Vapid

        Vapid.from_string(private_key=private_key)
        return True
    except Exception:
        pass
    try:
        from cryptography.hazmat.primitives import serialization

        serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
        return True
    except Exception:
        return False


def ensure_vapid_keys() -> dict[str, str]:
    existing = _load_json(VAPID_PATH, {})
    if existing.get("publicKey") and existing.get("privateKey"):
        priv = str(existing["privateKey"])
        if _vapid_usable(priv):
            return {
                "publicKey": str(existing["publicKey"]),
                "privateKey": priv,
                "subject": str(
                    existing.get("subject") or os.getenv("VAPID_SUBJECT", "mailto:daytrade@localhost")
                ),
            }
        print("[push] regenerating unusable VAPID private key", flush=True)

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    raw_pub = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(raw_pub).decode("utf-8").rstrip("=")
    # pywebpush prefers raw URL-safe base64 private scalar (not PEM)
    raw_priv = private_key.private_numbers().private_value.to_bytes(32, "big")
    priv_b64 = base64.urlsafe_b64encode(raw_priv).decode("utf-8").rstrip("=")
    keys = {
        "publicKey": public_b64,
        "privateKey": priv_b64,
        "privateKeyPem": private_pem,
        "subject": os.getenv("VAPID_SUBJECT", "mailto:daytrade@localhost"),
    }
    _save_json(VAPID_PATH, keys)
    return keys


def list_subscriptions() -> list[dict[str, Any]]:
    rows = _load_json(SUBS_PATH, [])
    return rows if isinstance(rows, list) else []


def save_subscription(sub: dict[str, Any]) -> None:
    endpoint = str((sub or {}).get("endpoint") or "")
    if not endpoint:
        return
    rows = [r for r in list_subscriptions() if str(r.get("endpoint") or "") != endpoint]
    rows.append(sub)
    _save_json(SUBS_PATH, rows[-40:])


def remove_subscription(endpoint: str) -> None:
    rows = [r for r in list_subscriptions() if str(r.get("endpoint") or "") != endpoint]
    _save_json(SUBS_PATH, rows)


def send_web_push(title: str, body: str, *, url: str = "/") -> int:
    try:
        from pywebpush import WebPushException, webpush
    except Exception as e:
        print(f"[push] pywebpush missing: {e}", flush=True)
        return 0

    keys = ensure_vapid_keys()
    payload = json.dumps({"title": title, "body": body, "url": url, "lang": "ar"}, ensure_ascii=False)
    ok = 0
    dead: list[str] = []
    jwt_bad = 0
    for sub in list_subscriptions():
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=keys["privateKey"],
                vapid_claims={"sub": keys["subject"]},
                timeout=8,
            )
            ok += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            body_txt = ""
            try:
                body_txt = (getattr(e, "response", None).text or "")[:120]
            except Exception:
                body_txt = str(e)[:120]
            # اشتراك قديم بمفتاح VAPID مختلف — احذفه حتى لا يبطئ كل دورة
            if status in (404, 410) or (status == 403 and "BadJwtToken" in body_txt):
                dead.append(str(sub.get("endpoint") or ""))
                if status == 403:
                    jwt_bad += 1
            print(f"[push] fail {status}: {e}", flush=True)
        except Exception as e:
            print(f"[push] error: {e}", flush=True)
    for ep in dead:
        remove_subscription(ep)
    if jwt_bad:
        print(f"[push] removed {jwt_bad} BadJwtToken subscription(s)", flush=True)
    return ok


def notify_urgent_from_status(status_path: Path | None = None) -> int:
    path = status_path or (ROOT / "docs" / "status.json")
    data = _load_json(path, {})
    urgent: list[str] = []
    for o in data.get("opportunities") or []:
        if not isinstance(o, dict):
            continue
        if o.get("urgent") or str(o.get("confidence") or "").upper() == "A":
            sym = str(o.get("symbol") or "").upper()
            if sym:
                urgent.append(sym)
    urgent = list(dict.fromkeys(urgent))[:8]
    if not urgent:
        return 0

    gate = _load_json(GATE_PATH, {"sent": {}})
    sent = gate.get("sent") if isinstance(gate.get("sent"), dict) else {}
    now = time.time()
    fresh = [s for s in urgent if now - float(sent.get(s) or 0) > 1800]
    if not fresh:
        return 0

    n = send_web_push("فرصة عاجلة", " · ".join(fresh), url="/")
    if n:
        for s in fresh:
            sent[s] = now
        gate["sent"] = {k: v for k, v in sent.items() if now - float(v) < 86400}
        _save_json(GATE_PATH, gate)
        print(f"[push] sent urgent={fresh} devices={n}", flush=True)
    return n
