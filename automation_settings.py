"""Persistent user policy for Job Hunter's application automation.

This stores pre-approval preferences only. Actual third-party application submission
is intentionally not enabled until a compliant, authenticated browser worker exists.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from database import connect, postgres_enabled

DEFAULTS = {
    "enabled": False,
    "min_score": 80,
    "max_per_run": 10,
    "auto_submit_enabled": False,
    "pause_on_captcha_otp": True,
    "pause_on_unknown_required_answer": True,
    "pause_on_legal_declaration": True,
    "allowed_portals": ["greenhouse.io", "lever.co"],
    "updated_at": None,
}

def _now():
    return datetime.now(timezone.utc).isoformat()

def init_automation_settings():
    with connect() as conn:
        if postgres_enabled():
            conn.execute("""CREATE TABLE IF NOT EXISTS automation_settings (
                id INTEGER PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS automation_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        row = conn.execute("SELECT id FROM automation_settings WHERE id=1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO automation_settings (id, config_json, updated_at) VALUES (1, ?, ?)",
                (json.dumps(DEFAULTS), _now()),
            )

def get_automation_settings():
    init_automation_settings()
    with connect() as conn:
        row = conn.execute("SELECT config_json, updated_at FROM automation_settings WHERE id=1").fetchone()
    config = dict(DEFAULTS)
    if row:
        try:
            config.update(json.loads(row["config_json"] or "{}"))
        except (TypeError, ValueError):
            pass
        config["updated_at"] = row["updated_at"]
    # Hard guard: policy configuration alone cannot enable submissions.
    config["auto_submit_enabled"] = False
    config["execution_status"] = "policy_only_no_browser_worker"
    return config

def save_automation_settings(payload):
    current = get_automation_settings()
    allowed = {
        "enabled", "min_score", "max_per_run", "pause_on_captcha_otp",
        "pause_on_unknown_required_answer", "pause_on_legal_declaration", "allowed_portals"
    }
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key in {"enabled", "pause_on_captcha_otp", "pause_on_unknown_required_answer", "pause_on_legal_declaration"}:
            if not isinstance(value, bool):
                raise ValueError(key + " must be a boolean")
            current[key] = value
        elif key == "min_score":
            if not isinstance(value, int) or isinstance(value, bool) or not 50 <= value <= 100:
                raise ValueError("min_score must be an integer from 50 to 100")
            current[key] = value
        elif key == "max_per_run":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 50:
                raise ValueError("max_per_run must be an integer from 1 to 50")
            current[key] = value
        elif key == "allowed_portals":
            if not isinstance(value, list) or not value or len(value) > 20 or any(not isinstance(x, str) or not x.strip() for x in value):
                raise ValueError("allowed_portals must be a non-empty list of hostnames")
            cleaned = list(dict.fromkeys(x.strip().lower() for x in value))
            if any("/" in x or ":" in x or "@" in x for x in cleaned):
                raise ValueError("allowed_portals must contain hostnames only")
            current[key] = cleaned
    current["auto_submit_enabled"] = False
    current["updated_at"] = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE automation_settings SET config_json=?, updated_at=? WHERE id=1",
            (json.dumps(current), current["updated_at"]),
        )
    return get_automation_settings()
