from __future__ import annotations
import os
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from backend_services import (
    ROLES, STATUSES, add_evidence, assign_complaint, audit_events, authenticate,
    create_user, evidence_for_user, export_csv, get_complaint, get_user,
    hotspots, public_hotspots, init_db, list_complaints, list_users, submit_complaint, summary,
    update_complaint,
)

st.set_page_config(page_title="SwasthyaGrid | Civic Intelligence", page_icon="🌐", layout="wide", initial_sidebar_state="expanded")

CATEGORY_OPTIONS = ["Water & Drainage", "Road & Transport", "Waste & Sanitation", "Public Health", "Electricity & Lighting", "Other Civic Issue"]
SEVERITIES = ["Low", "Medium", "High", "Critical"]


def secret(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def notice(message: str, kind: str = "success") -> None:
    st.session_state["sg_notice"] = (message, kind)


def show_notice() -> None:
    item = st.session_state.pop("sg_notice", None)
    if item:
        message, kind = item
        getattr(st, kind if kind in {"success", "info", "warning", "error"} else "info")(message)


def uploaded_files(items: list[Any] | None) -> list[dict[str, Any]]:
    result = []
    total_bytes = 0
    max_mb = int(secret("MAX_UPLOAD_MB", "10") or "10")
    for item in items or []:
        data = item.getvalue()
        total_bytes += len(data)
        if len(data) > max_mb * 1024 * 1024:
            st.warning(f"{item.name} exceeds the {max_mb} MB per-file limit and was skipped.")
            continue
        result.append({"filename": item.name, "mime_type": item.type or "application/octet-stream", "data": data})
    if total_bytes > max_mb * 1024 * 1024 * 3:
        st.warning("Large total upload selected. Consider attaching only the most useful evidence.")
    return result


def table_for_cases(cases: list[dict[str, Any]]) -> pd.DataFrame:
    columns = ["id", "title", "category", "area", "severity", "risk_level", "priority_score", "status", "assigned_officer", "representative", "evidence_count", "created_at"]
    rows = [{k: c.get(k, "") for k in columns} for c in cases]
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df = df.rename(columns={"id": "Case ID", "title": "Title", "category": "Category", "area": "Area", "severity": "Severity", "risk_level": "Risk", "priority_score": "Priority", "status": "Status", "assigned_officer": "Field Officer", "representative": "Representative", "evidence_count": "Evidence", "created_at": "Reported At"})
    return df


def render_metrics(user: dict[str, Any]) -> None:
    s = summary(user)
    cols = st.columns(5)
    for col, label, key in zip(cols, ["Total reports", "Open cases", "Critical alerts", "Escalated", "Resolved"], ["total", "open", "critical", "escalated", "resolved"]):
        col.metric(label, s[key])


def render_hotspots(user: dict[str, Any]) -> None:
    st.subheader("🗺️ CivicPulse · Emerging hotspot signals")
    # Citizens see aggregate-only public signals rather than only their own cases.
    rows = public_hotspots() if user["role"] == "Citizen" else hotspots(user)
    if rows:
        st.caption("Signals require at least three reports for the same area/category. They are not official outbreak declarations.")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No repeated area/category signal yet. Hotspots appear after at least three related reports.")


def render_evidence(user: dict[str, Any], complaint_id: str) -> None:
    items = evidence_for_user(user, complaint_id)
    if not items:
        st.caption("No evidence available for this case, or you do not have permission to view it.")
        return
    st.markdown("**Attached evidence**")
    for item in items:
        left, right = st.columns([3, 1])
        left.write(f"📎 **{item['filename']}** · {item['mime_type']} · uploaded by {item['uploaded_by']}")
        right.download_button("Download", data=item["data"], file_name=item["filename"], mime=item["mime_type"], key=f"dl_{item['id']}")
        if item["note"]:
            st.caption(item["note"])


def render_login() -> None:
    st.markdown("<div style='padding:14px 0 4px 0'><span style='font-size:38px'>🌐</span> <span style='font-size:34px;font-weight:800;color:#1b4965'>SwasthyaGrid</span></div>", unsafe_allow_html=True)
    st.subheader("Civic Intelligence & Health-Risk Triage")
    st.write("Report local issues, coordinate field action, flag emerging hotspots, and track resolution with an auditable workflow.")
    left, right = st.columns([1.05, 0.95], gap="large")
    with left:
        st.markdown("### Sign in")
        with st.form("login_form"):
            username = st.text_input("User ID", placeholder="Enter your User ID")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            user = authenticate(username, password)
            if user:
                st.session_state["sg_user"] = {k: user[k] for k in ("id", "username", "role", "full_name", "jurisdiction", "active")}
                notice(f"Welcome, {user['full_name']}!")
                st.rerun()
            else:
                st.error("Login failed. Check the User ID and password.")
        st.caption("For a quick demo, use one of the sample accounts shown to the right. Change demo credentials before any public or real-data use.")
    with right:
        st.markdown("### Demo access")
        st.warning("These are public demo credentials. Do not use them for production data.")
        accounts = [
            ("Admin", "admin", "Admin@12345"),
            ("Field Officer", "officer", "Officer@12345"),
            ("Representative", "representative", "Rep@123456"),
            ("Citizen", "citizen", "Citizen@12345"),
        ]
        for role, uid, pwd in accounts:
            st.markdown(f"**{role}** · User ID: `{uid}` · Password: `{pwd}`")
    st.divider()
    st.caption("Prototype notice: risk scores are advisory, not medical diagnoses or proof that a report is true. Suspected emergencies require real-world emergency services and human follow-up.")


def render_citizen(user: dict[str, Any]) -> None:
    st.title("👋 Welcome, Citizen")
    st.write("Raise a civic issue, attach evidence, and follow its progress.")
    render_metrics(user)
    st.divider()
    st.subheader("📝 Raise a complaint")
    api_key = secret("GEMINI_API_KEY")
    with st.form("complaint_form", clear_on_submit=True):
        title = st.text_input("Short title", placeholder="e.g. Drain overflowing near bus stop", max_chars=140)
        c1, c2 = st.columns(2)
        category = c1.selectbox("Issue category", CATEGORY_OPTIONS)
        severity = c2.selectbox("How urgent does it seem?", SEVERITIES, index=1)
        area = st.text_input("Ward / area", value=user.get("jurisdiction", "Central Ward"), max_chars=100)
        address = st.text_input("Landmark or address (optional)", max_chars=240)
        description = st.text_area("Describe the issue", placeholder="What happened, where, and when? Avoid including sensitive personal information.", height=120, max_chars=4000)
        evidence = st.file_uploader("Attach photos, audio or video", type=["png", "jpg", "jpeg", "webp", "mp3", "wav", "m4a", "mp4", "mov", "pdf"], accept_multiple_files=True)
        consent = False
        if api_key:
            consent = st.checkbox("I consent to sending the report text and one attached image to the configured Gemini service for an advisory relevance check. The report will still be kept if AI is unavailable.")
        submit = st.form_submit_button("Submit complaint", type="primary", use_container_width=True)
    if submit:
        if not title.strip() or len(description.strip()) < 8:
            st.error("Please enter a title and a more detailed description.")
        else:
            files = uploaded_files(evidence)
            result = submit_complaint(user, title, category, description, area, address, severity, files,
                                      allow_external_ai=bool(consent and api_key), api_key=api_key if consent else None)
            notice(f"Complaint {result['id']} submitted. Current status: {result['status']}.")
            st.rerun()
    cases = list_complaints(user)
    st.divider()
    st.subheader("📍 My complaints and tracking")
    st.dataframe(table_for_cases(cases), use_container_width=True, hide_index=True)
    if cases:
        case_id = st.selectbox("View a complaint", [c["id"] for c in cases], key="citizen_case_select")
        case = get_complaint(case_id)
        if case:
            with st.container(border=True):
                st.markdown(f"### {case['id']} · {case['title']}")
                st.write(f"**Status:** {case['status']} · **Risk:** {case['risk_level']} · **Priority score:** {case['priority_score']}")
                st.write(case["description"])
                st.info(f"Triage: {case['ai_validation']} — {case['ai_reason']}")
                if case.get("action_taken"):
                    st.write(f"**Action taken:** {case['action_taken']}")
                if case.get("remarks"):
                    st.write(f"**Latest remarks:** {case['remarks']}")
                render_evidence(user, case_id)
            if case["status"] == "Resolved":
                if st.button("Reopen this complaint", key=f"reopen_{case_id}"):
                    ok, msg = update_complaint(user, case_id, "Reopened", case.get("action_taken", ""), "Citizen requested review of the resolution.")
                    notice(msg, "success" if ok else "error")
                    st.rerun()


def render_officer(user: dict[str, Any]) -> None:
    st.title("🧰 Field Officer Console")
    st.write(f"Jurisdiction: **{user.get('jurisdiction', 'General')}** · Review reports, record work, and upload evidence.")
    render_metrics(user)
    cases = list_complaints(user)
    st.subheader("Assigned and area complaints")
    st.dataframe(table_for_cases(cases), use_container_width=True, hide_index=True)
    if not cases:
        st.info("No reports are currently assigned to you or visible in your area.")
        return
    case_id = st.selectbox("Choose a complaint", [c["id"] for c in cases], key="officer_case_select")
    case = get_complaint(case_id)
    if not case:
        return
    with st.container(border=True):
        st.markdown(f"### {case['id']} · {case['title']}")
        st.write(f"**Category:** {case['category']} · **Area:** {case['area']} · **Risk:** {case['risk_level']} · **Status:** {case['status']}")
        st.write(case["description"])
        st.info(f"Triage note: {case['ai_validation']} — {case['ai_reason']}")
        render_evidence(user, case_id)
    with st.form("officer_update_form"):
        status = st.selectbox("Update status", STATUSES, index=STATUSES.index(case["status"]) if case["status"] in STATUSES else 0)
        action_taken = st.text_area("Action taken (required to resolve)", value=case.get("action_taken", ""), max_chars=2000)
        remarks = st.text_area("Officer remarks / next steps", value=case.get("remarks", ""), max_chars=2000)
        attachments = st.file_uploader("Attach site evidence", type=["png", "jpg", "jpeg", "webp", "mp3", "wav", "m4a", "mp4", "mov", "pdf"], accept_multiple_files=True, key="officer_evidence")
        save = st.form_submit_button("Save update", type="primary")
    if save:
        ok, msg = update_complaint(user, case_id, status, action_taken, remarks, uploaded_files(attachments))
        notice(msg, "success" if ok else "error")
        st.rerun()
    if st.button("Forward to Representative / escalate", key=f"escalate_{case_id}"):
        ok, msg = update_complaint(user, case_id, "Escalated", case.get("action_taken", ""), (case.get("remarks", "") + "\nOfficer requested representative guidance.").strip())
        notice(msg, "success" if ok else "error")
        st.rerun()


def render_admin(user: dict[str, Any]) -> None:
    st.title("🛡️ Admin Control Centre")
    st.write("Manage roles, assign work, monitor risk signals, and export public-service performance data.")
    render_metrics(user)
    all_cases = list_complaints(user)
    tab_overview, tab_assign, tab_users, tab_audit = st.tabs(["Overview & reports", "Assignment & case control", "User management", "Audit trail"])
    with tab_overview:
        render_hotspots(user)
        st.subheader("All complaints")
        st.dataframe(table_for_cases(all_cases), use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download privacy-conscious CSV", data=export_csv(all_cases), file_name=f"swasthyagrid_reports_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", mime="text/csv")
        if all_cases:
            counts = pd.DataFrame([{"Status": status, "Reports": sum(c["status"] == status for c in all_cases)} for status in STATUSES])
            st.subheader("Workflow status distribution")
            st.bar_chart(counts.set_index("Status"))
    with tab_assign:
        if not all_cases:
            st.info("No complaints yet. Create a citizen report first to test assignment.")
        else:
            cid = st.selectbox("Complaint", [c["id"] for c in all_cases], key="admin_assign_case")
            case = get_complaint(cid)
            officers = list_users("Field Officer")
            reps = list_users("Representative")
            officer_names = [""] + [u["username"] for u in officers]
            rep_names = [""] + [u["username"] for u in reps]
            with st.form("assignment_form"):
                officer = st.selectbox("Assign Field Officer", officer_names, index=officer_names.index(case["assigned_officer"]) if case and case["assigned_officer"] in officer_names else 0)
                representative = st.selectbox("Assign Representative", rep_names, index=rep_names.index(case["representative"]) if case and case["representative"] in rep_names else 0)
                assign = st.form_submit_button("Save assignment", type="primary")
            if assign:
                ok, msg = assign_complaint(user["username"], cid, officer, representative)
                notice(msg, "success" if ok else "error")
                st.rerun()
            if case:
                st.markdown(f"**{cid} · {case['title']}** — {case['status']}")
                st.write(case["description"])
                with st.form("admin_status_form"):
                    status = st.selectbox("Admin status override", STATUSES, index=STATUSES.index(case["status"]) if case["status"] in STATUSES else 0)
                    action = st.text_area("Action taken", value=case.get("action_taken", ""))
                    remarks = st.text_area("Admin remarks", value=case.get("remarks", ""))
                    update = st.form_submit_button("Update case status")
                if update:
                    ok, msg = update_complaint(user, cid, status, action, remarks)
                    notice(msg, "success" if ok else "error")
                    st.rerun()
    with tab_users:
        st.subheader("Create account")
        with st.form("create_user_form"):
            c1, c2 = st.columns(2)
            username = c1.text_input("User ID", max_chars=60)
            full_name = c2.text_input("Full name", max_chars=100)
            c3, c4 = st.columns(2)
            role = c3.selectbox("Role", list(ROLES))
            jurisdiction = c4.text_input("Ward / jurisdiction", value="Central Ward", max_chars=100)
            password = st.text_input("Temporary password (minimum 10 characters)", type="password", max_chars=200)
            create = st.form_submit_button("Create account", type="primary")
        if create:
            ok, msg = create_user(user["username"], username, password, role, full_name, jurisdiction)
            notice(msg, "success" if ok else "error")
            st.rerun()
        st.subheader("Current users")
        st.dataframe(pd.DataFrame(list_users()).drop(columns=["id", "active"], errors="ignore"), use_container_width=True, hide_index=True)
    with tab_audit:
        st.subheader("Recent system audit events")
        st.dataframe(pd.DataFrame(audit_events()), use_container_width=True, hide_index=True)


def render_representative(user: dict[str, Any]) -> None:
    title = "MLA" if "mla" in user.get("full_name", "").lower() else "Representative"
    st.title(f"🏛️ Welcome, {title}")
    st.write(f"Oversight area: **{user.get('jurisdiction', 'General')}**. Review escalations and service outcomes without exposing unnecessary citizen details.")
    render_metrics(user)
    render_hotspots(user)
    cases = list_complaints(user)
    st.subheader("Oversight and escalated reports")
    st.dataframe(table_for_cases(cases), use_container_width=True, hide_index=True)
    st.download_button("⬇️ Download oversight report", data=export_csv(cases), file_name=f"oversight_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")
    if cases:
        cid = st.selectbox("Open a case for oversight", [c["id"] for c in cases], key="rep_case_select")
        case = get_complaint(cid)
        if case:
            st.markdown(f"### {case['id']} · {case['title']}")
            st.write(f"**Status:** {case['status']} · **Risk:** {case['risk_level']} · **Area:** {case['area']}")
            st.write(case["description"])
            if case.get("action_taken"):
                st.write(f"**Action taken:** {case['action_taken']}")
            render_evidence(user, cid)
            with st.form("rep_guidance_form"):
                guidance = st.text_area("Representative guidance / oversight note", value=case.get("remarks", ""), max_chars=2000)
                send = st.form_submit_button("Save oversight note")
            if send:
                ok, msg = update_complaint(user, cid, case["status"], case.get("action_taken", ""), guidance)
                notice(msg, "success" if ok else "error")
                st.rerun()


def main() -> None:
    init_db()
    show_notice()
    if "sg_user" not in st.session_state:
        render_login()
        return
    user = st.session_state["sg_user"]
    fresh = get_user(user["username"])
    if not fresh or not fresh.get("active"):
        st.session_state.pop("sg_user", None)
        st.error("This account is inactive. Please contact the Admin.")
        st.rerun()
    user.update({k: fresh[k] for k in ("role", "full_name", "jurisdiction", "active")})
    with st.sidebar:
        st.markdown("# 🌐 SwasthyaGrid")
        st.caption("Civic Intelligence Platform")
        st.divider()
        st.write(f"**{user['full_name']}**")
        st.caption(f"{user['role']} · {user['jurisdiction']}")
        if st.button("Sign out", use_container_width=True):
            st.session_state.pop("sg_user", None)
            st.rerun()
        st.divider()
        st.caption("Prototype • AI advisory only • verify emergencies through real-world channels")
    if user["role"] == "Citizen":
        render_citizen(user)
    elif user["role"] == "Field Officer":
        render_officer(user)
    elif user["role"] == "Admin":
        render_admin(user)
    elif user["role"] == "Representative":
        render_representative(user)
    else:
        st.error("Unknown role. Contact your administrator.")


main()
