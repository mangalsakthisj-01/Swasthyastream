"""Flat-file backend services for SwasthyaGrid (SQLite, auth, workflow, audit)."""
from __future__ import annotations
import csv
import hashlib
import hmac
import io
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend_triage import assess_complaint

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SWASTHYAGRID_DATA_DIR", str(APP_DIR / "data")))
DB_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_DIR / "swasthyagrid.db")))
ROLES = ("Citizen", "Field Officer", "Admin", "Representative")
STATUSES = ("Submitted", "Triaged", "Assigned", "In Progress", "Escalated", "Resolved", "Reopened")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"pbkdf2_sha256$260000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = encoded.split("$")
        candidate = hashlib.pbkdf2_hmac(algo.split("_")[-1], password.encode(), bytes.fromhex(salt_hex), int(iterations)).hex()
        return hmac.compare_digest(candidate, digest_hex)
    except Exception:
        return False


def audit(con: sqlite3.Connection, actor: str, action: str, complaint_id: str | None = None, detail: str = "") -> None:
    con.execute("INSERT INTO audit_logs(actor,action,complaint_id,detail,created_at) VALUES(?,?,?,?,?)", (actor, action, complaint_id, detail[:1000], now()))


def init_db() -> None:
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL, full_name TEXT NOT NULL,
          jurisdiction TEXT NOT NULL DEFAULT 'General', active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS complaints (
          id TEXT PRIMARY KEY, citizen_username TEXT NOT NULL, complainant_name TEXT NOT NULL,
          title TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
          area TEXT NOT NULL, address TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL,
          risk_level TEXT NOT NULL, priority_score INTEGER NOT NULL,
          ai_validation TEXT NOT NULL, ai_confidence INTEGER NOT NULL, ai_reason TEXT NOT NULL,
          triage_provider TEXT NOT NULL, review_required INTEGER NOT NULL,
          critical_override INTEGER NOT NULL, status TEXT NOT NULL,
          assigned_officer TEXT NOT NULL DEFAULT '', representative TEXT NOT NULL DEFAULT '',
          action_taken TEXT NOT NULL DEFAULT '', remarks TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS evidence (
          id INTEGER PRIMARY KEY AUTOINCREMENT, complaint_id TEXT NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
          uploaded_by TEXT NOT NULL, filename TEXT NOT NULL, mime_type TEXT NOT NULL,
          data BLOB NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
          action TEXT NOT NULL, complaint_id TEXT, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_complaints_area_category ON complaints(area,category);
        CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status);
        CREATE INDEX IF NOT EXISTS idx_evidence_complaint ON evidence(complaint_id);
        """)
        users = con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        allow_demo = os.getenv("ALLOW_DEMO_LOGINS", "true").lower() == "true"
        bootstrap_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if users == 0 and allow_demo:
            seed = [
                ("admin", "Admin@12345", "Admin", "System Administrator", "All Areas"),
                ("officer", "Officer@12345", "Field Officer", "Sample Field Officer", "Central Ward"),
                ("representative", "Rep@123456", "Representative", "Sample Representative", "Central Ward"),
                ("citizen", "Citizen@12345", "Citizen", "Sample Citizen", "Central Ward"),
            ]
            for username, password, role, name, area in seed:
                con.execute("INSERT INTO users(username,password_hash,role,full_name,jurisdiction,created_at) VALUES(?,?,?,?,?,?)",
                            (username, hash_password(password), role, name, area, now()))
        elif users == 0 and bootstrap_password:
            con.execute("INSERT INTO users(username,password_hash,role,full_name,jurisdiction,created_at) VALUES(?,?,?,?,?,?)",
                        ("admin", hash_password(bootstrap_password), "Admin", "System Administrator", "All Areas", now()))


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            with connect() as con2:
                audit(con2, username.strip() or "unknown", "LOGIN_FAILED", detail="Invalid credentials")
            return None
        audit(con, row["username"], "LOGIN_SUCCESS")
        return dict(row)


def list_users(role: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        if role:
            rows = con.execute("SELECT id,username,role,full_name,jurisdiction,active,created_at FROM users WHERE role=? ORDER BY full_name", (role,)).fetchall()
        else:
            rows = con.execute("SELECT id,username,role,full_name,jurisdiction,active,created_at FROM users ORDER BY role,full_name").fetchall()
        return [dict(x) for x in rows]


def create_user(actor: str, username: str, password: str, role: str, full_name: str, jurisdiction: str) -> tuple[bool, str]:
    init_db()
    username = username.strip()
    if len(username) < 3 or not username.replace("_", "").replace("-", "").isalnum():
        return False, "User ID must be at least 3 characters using letters, digits, _ or -"
    if len(password) < 10:
        return False, "Password must have at least 10 characters"
    if role not in ROLES:
        return False, "Invalid role"
    try:
        with connect() as con:
            con.execute("INSERT INTO users(username,password_hash,role,full_name,jurisdiction,created_at) VALUES(?,?,?,?,?,?)",
                        (username, hash_password(password), role, full_name.strip() or username, jurisdiction.strip() or "General", now()))
            audit(con, actor, "USER_CREATED", detail=f"username={username}; role={role}")
        return True, f"Created account {username}"
    except sqlite3.IntegrityError:
        return False, "That User ID already exists"


def get_user(username: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT id,username,role,full_name,jurisdiction,active,created_at FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def submit_complaint(citizen: dict[str, Any], title: str, category: str, description: str, area: str,
                     address: str, severity: str, files: list[dict[str, Any]] | None = None,
                     allow_external_ai: bool = False, api_key: str | None = None) -> dict[str, Any]:
    init_db()
    files = files or []
    image_file = next((f for f in files if f.get("mime_type", "").startswith("image/")), None)
    triage = assess_complaint(category, description, severity,
                              image_file.get("data") if image_file else None,
                              image_file.get("mime_type") if image_file else None,
                              allow_external_ai, api_key)
    complaint_id = "SG-" + uuid.uuid4().hex[:8].upper()
    status = "Escalated" if triage["critical_override"] else "Triaged"
    officer = ""
    rep = ""
    if status == "Escalated":
        officers = list_users("Field Officer")
        reps = list_users("Representative")
        officer = next((u["username"] for u in officers if u["jurisdiction"] in {area, "All Areas", "General"}), officers[0]["username"] if officers else "")
        rep = next((u["username"] for u in reps if u["jurisdiction"] in {area, "All Areas", "General"}), reps[0]["username"] if reps else "")
    with connect() as con:
        con.execute("""INSERT INTO complaints(id,citizen_username,complainant_name,title,category,description,area,address,severity,risk_level,priority_score,ai_validation,ai_confidence,ai_reason,triage_provider,review_required,critical_override,status,assigned_officer,representative,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (complaint_id, citizen["username"], citizen.get("full_name", citizen["username"]), title.strip(), category, description.strip(), area.strip() or "General", address.strip(), triage["severity"], triage["risk_level"], triage["priority_score"], triage["ai_validation"], triage["ai_confidence"], triage["ai_reason"], triage["triage_provider"], int(triage["review_required"]), int(triage["critical_override"]), status, officer, rep, now(), now()))
        for f in files:
            data = f.get("data", b"")
            if not data:
                continue
            con.execute("INSERT INTO evidence(complaint_id,uploaded_by,filename,mime_type,data,note,created_at) VALUES(?,?,?,?,?,?,?)",
                        (complaint_id, citizen["username"], Path(f.get("filename", "evidence.bin")).name[:200], f.get("mime_type", "application/octet-stream")[:120], data, "Citizen-submitted evidence", now()))
        audit(con, citizen["username"], "COMPLAINT_SUBMITTED", complaint_id, f"category={category}; risk={triage['risk_level']}; score={triage['priority_score']}")
        if triage["critical_override"]:
            audit(con, "system", "CRITICAL_BYPASS", complaint_id, "Routed to escalated queue")
        result = con.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
        return dict(result)


def _visible_clause(user: dict[str, Any]) -> tuple[str, list[Any]]:
    role, username, area = user["role"], user["username"], user.get("jurisdiction", "General")
    if role == "Citizen":
        return "citizen_username=?", [username]
    if role == "Field Officer":
        return "(assigned_officer=? OR (assigned_officer='' AND area IN (?, 'General')))", [username, area]
    if role == "Representative":
        return "(representative=? OR (representative='' AND area IN (?, 'General')) OR status='Escalated')", [username, area]
    return "1=1", []


def list_complaints(user: dict[str, Any], limit: int = 500) -> list[dict[str, Any]]:
    init_db()
    clause, params = _visible_clause(user)
    with connect() as con:
        rows = con.execute(f"SELECT c.*, (SELECT COUNT(*) FROM evidence e WHERE e.complaint_id=c.id) AS evidence_count FROM complaints c WHERE {clause} ORDER BY c.critical_override DESC,c.priority_score DESC,c.created_at DESC LIMIT ?", [*params, limit]).fetchall()
        return [dict(r) for r in rows]


def get_complaint(complaint_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
        return dict(row) if row else None


def assign_complaint(actor: str, complaint_id: str, officer: str, representative: str) -> tuple[bool, str]:
    with connect() as con:
        row = con.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
        if not row:
            return False, "Complaint not found"
        if officer and not con.execute("SELECT 1 FROM users WHERE username=? AND role='Field Officer' AND active=1", (officer,)).fetchone():
            return False, "Choose a valid active Field Officer"
        if representative and not con.execute("SELECT 1 FROM users WHERE username=? AND role='Representative' AND active=1", (representative,)).fetchone():
            return False, "Choose a valid active Representative"
        status = "Assigned" if row["status"] in {"Submitted", "Triaged", "Reopened"} and officer else row["status"]
        con.execute("UPDATE complaints SET assigned_officer=?,representative=?,status=?,updated_at=? WHERE id=?", (officer, representative, status, now(), complaint_id))
        audit(con, actor, "COMPLAINT_ASSIGNED", complaint_id, f"officer={officer}; representative={representative}")
    return True, "Assignment saved"


def update_complaint(actor: dict[str, Any], complaint_id: str, status: str, action_taken: str, remarks: str,
                     files: list[dict[str, Any]] | None = None) -> tuple[bool, str]:
    if status not in STATUSES:
        return False, "Invalid status"
    with connect() as con:
        row = con.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
        if not row:
            return False, "Complaint not found"
        role = actor["role"]
        if role == "Citizen" and row["citizen_username"] != actor["username"]:
            return False, "You can update only your own reports"
        if role == "Field Officer" and row["assigned_officer"] not in ("", actor["username"]):
            return False, "This complaint is assigned to another officer"
        if role == "Representative" and row["representative"] not in ("", actor["username"]) and row["status"] != "Escalated":
            return False, "This complaint is not assigned to your oversight queue"
        if status == "Resolved" and not action_taken.strip():
            return False, "Record the action taken before marking a case resolved"
        if role == "Citizen" and status not in {"Reopened"}:
            return False, "Citizens may reopen resolved cases, not change operational statuses"
        resolved_at = now() if status == "Resolved" else None
        con.execute("UPDATE complaints SET status=?,action_taken=?,remarks=?,updated_at=?,resolved_at=? WHERE id=?",
                    (status, action_taken.strip(), remarks.strip(), now(), resolved_at, complaint_id))
        for f in (files or []):
            data = f.get("data", b"")
            if data:
                con.execute("INSERT INTO evidence(complaint_id,uploaded_by,filename,mime_type,data,note,created_at) VALUES(?,?,?,?,?,?,?)",
                            (complaint_id, actor["username"], Path(f.get("filename", "evidence.bin")).name[:200], f.get("mime_type", "application/octet-stream")[:120], data, "Field update evidence", now()))
        audit(con, actor["username"], "COMPLAINT_STATUS_UPDATED", complaint_id, f"status={status}; action={action_taken[:150]}")
    return True, "Complaint updated"


def add_evidence(actor: str, complaint_id: str, filename: str, mime_type: str, data: bytes, note: str = "") -> None:
    with connect() as con:
        con.execute("INSERT INTO evidence(complaint_id,uploaded_by,filename,mime_type,data,note,created_at) VALUES(?,?,?,?,?,?,?)",
                    (complaint_id, actor, Path(filename).name[:200], mime_type[:120], data, note[:500], now()))
        audit(con, actor, "EVIDENCE_UPLOADED", complaint_id, filename)


def evidence_for(complaint_id: str) -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT id,complaint_id,uploaded_by,filename,mime_type,data,note,created_at FROM evidence WHERE complaint_id=? ORDER BY created_at", (complaint_id,)).fetchall()]


def summary(user: dict[str, Any]) -> dict[str, int]:
    cases = list_complaints(user)
    return {
        "total": len(cases), "open": sum(c["status"] not in {"Resolved"} for c in cases),
        "critical": sum(bool(c["critical_override"]) for c in cases),
        "escalated": sum(c["status"] == "Escalated" for c in cases),
        "resolved": sum(c["status"] == "Resolved" for c in cases),
        "review": sum(bool(c["review_required"]) for c in cases),
    }


def hotspots(user: dict[str, Any]) -> list[dict[str, Any]]:
    cases = list_complaints(user)
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in cases:
        buckets.setdefault((c["area"], c["category"]), []).append(c)
    result = []
    for (area, category), items in buckets.items():
        if len(items) >= 3:
            result.append({"Area": area, "Category": category, "Reports": len(items), "Open": sum(x["status"] != "Resolved" for x in items), "Critical": sum(bool(x["critical_override"]) for x in items)})
    return sorted(result, key=lambda x: (x["Critical"], x["Reports"]), reverse=True)


def public_hotspots() -> list[dict[str, Any]]:
    """Return aggregate-only hotspot signals; no citizen identifiers are exposed."""
    init_db()
    with connect() as con:
        rows = con.execute(
            "SELECT area AS Area, category AS Category, COUNT(*) AS Reports, "
            "SUM(CASE WHEN status != 'Resolved' THEN 1 ELSE 0 END) AS Open, "
            "SUM(critical_override) AS Critical FROM complaints "
            "GROUP BY area, category HAVING COUNT(*) >= 3 "
            "ORDER BY SUM(critical_override) DESC, COUNT(*) DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def audit_events(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT id,actor,action,complaint_id,detail,created_at FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def export_csv(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b"No complaints to export\n"
    # Avoid exporting unnecessary personal name fields by default.
    fields = ["id", "title", "category", "area", "severity", "risk_level", "priority_score", "status", "assigned_officer", "representative", "action_taken", "remarks", "created_at", "updated_at"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def evidence_for_user(user: dict[str, Any], complaint_id: str) -> list[dict[str, Any]]:
    c = get_complaint(complaint_id)
    if not c:
        return []
    if user["role"] == "Citizen" and c["citizen_username"] != user["username"]:
        return []
    allowed = list_complaints(user)
    if not any(x["id"] == complaint_id for x in allowed):
        return []
    return evidence_for(complaint_id)
