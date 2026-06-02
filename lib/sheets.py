"""Google Sheets 連携（gspread）。
streamlit/streamlit_new_item.py のパターンを踏襲し、st.secrets から
google_credentials / gspread_token を JSON に展開して OAuth で gspread を使う。"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import gspread
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SPREADSHEET_ID = "1xgn0MDZRLQeuvgBXtDNaHyRVID6zZRVXDGXALI4YOjM"

JOBS_SHEET = "jobs"
USERS_SHEET = "users"
TOOLS_SHEET = "tools"

CREDENTIALS_PATH = "google_credentials.json"
TOKEN_PATH = "gspread_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# 各シートのヘッダ定義（順番固定）
JOBS_HEADERS = [
    "job_id", "user_email", "tool_name", "params_json",
    "status", "requested_at", "started_at", "finished_at",
    "log_url", "error", "worker_host",
]
USERS_HEADERS = ["email", "display_name", "role", "allowed_tools", "active"]
TOOLS_HEADERS = [
    "tool_name", "display_name", "description",
    "required_role", "worker_host", "command_template", "params_schema_json",
]


def _recursive_dict(d):
    if hasattr(d, "items"):
        return {k: _recursive_dict(v) for k, v in d.items()}
    return d


def materialize_secrets():
    """st.secrets の google_credentials / gspread_token を JSON ファイルへ展開する。"""
    try:
        if "google_credentials" in st.secrets:
            with open(CREDENTIALS_PATH, "w") as f:
                f.write(json.dumps(_recursive_dict(st.secrets["google_credentials"])))
        if "gspread_token" in st.secrets:
            with open(TOKEN_PATH, "w") as f:
                f.write(json.dumps(_recursive_dict(st.secrets["gspread_token"])))
    except Exception:
        pass


def get_client() -> gspread.Client | None:
    """gspread クライアントを返す。トークン無効なら None。"""
    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception:
            creds = None
    if creds and creds.valid:
        return gspread.authorize(creds)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
            return gspread.authorize(creds)
        except Exception:
            pass
    return None


@st.cache_resource(ttl=3600)
def get_spreadsheet():
    client = get_client()
    if client is None:
        raise RuntimeError("gspread の認証に失敗しました（secrets の gspread_token を確認）")
    return client.open_by_key(SPREADSHEET_ID)


# ──────────────────────────────────────
# 初期化（シート3枚 + ヘッダ）
# ──────────────────────────────────────
def ensure_sheets_initialized() -> list[str]:
    """jobs / users / tools の3シートが無ければ作成し、ヘッダ行を入れる。
    返り値は新規作成したシート名のリスト。"""
    ss = get_spreadsheet()
    existing = {ws.title for ws in ss.worksheets()}
    created: list[str] = []

    def ensure(name: str, headers: list[str], rows: int = 200):
        if name not in existing:
            ws = ss.add_worksheet(title=name, rows=rows, cols=max(20, len(headers)))
            ws.update("A1", [headers])
            created.append(name)
        else:
            ws = ss.worksheet(name)
            first_row = ws.row_values(1)
            if first_row != headers:
                ws.update("A1", [headers])

    ensure(JOBS_SHEET, JOBS_HEADERS, rows=1000)
    ensure(USERS_SHEET, USERS_HEADERS)
    ensure(TOOLS_SHEET, TOOLS_HEADERS)
    return created


# ──────────────────────────────────────
# users
# ──────────────────────────────────────
def get_user(email: str) -> dict | None:
    ss = get_spreadsheet()
    rows = ss.worksheet(USERS_SHEET).get_all_records()
    for r in rows:
        if str(r.get("email", "")).strip().lower() == email.strip().lower():
            return r
    return None


def user_allowed_tools(user: dict, all_tool_names: list[str]) -> list[str]:
    raw = str(user.get("allowed_tools", "")).strip()
    if raw == "*":
        return list(all_tool_names)
    return [t.strip() for t in raw.split(",") if t.strip()]


def user_is_active(user: dict | None) -> bool:
    if not user:
        return False
    v = str(user.get("active", "")).strip().upper()
    return v in {"TRUE", "1", "YES"}


# ──────────────────────────────────────
# tools
# ──────────────────────────────────────
@st.cache_data(ttl=60)
def list_tools() -> list[dict]:
    ss = get_spreadsheet()
    return ss.worksheet(TOOLS_SHEET).get_all_records()


def get_tool(tool_name: str) -> dict | None:
    for t in list_tools():
        if t.get("tool_name") == tool_name:
            return t
    return None


# ──────────────────────────────────────
# jobs
# ──────────────────────────────────────
def _now_jst_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def enqueue_job(user_email: str, tool_name: str, params: dict, worker_host: str) -> str:
    """jobs シートに 1 行追加して job_id を返す。"""
    ss = get_spreadsheet()
    ws = ss.worksheet(JOBS_SHEET)
    job_id = uuid.uuid4().hex[:12]
    row = [
        job_id,
        user_email,
        tool_name,
        json.dumps(params, ensure_ascii=False),
        "queued",
        _now_jst_iso(),
        "",  # started_at
        "",  # finished_at
        "",  # log_url
        "",  # error
        worker_host,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    return job_id


def list_jobs_for_user(user_email: str, limit: int = 50) -> list[dict]:
    ss = get_spreadsheet()
    rows = ss.worksheet(JOBS_SHEET).get_all_records()
    mine = [r for r in rows if str(r.get("user_email", "")).strip().lower() == user_email.strip().lower()]
    mine.sort(key=lambda r: r.get("requested_at", ""), reverse=True)
    return mine[:limit]


def list_all_jobs(limit: int = 100) -> list[dict]:
    ss = get_spreadsheet()
    rows = ss.worksheet(JOBS_SHEET).get_all_records()
    rows.sort(key=lambda r: r.get("requested_at", ""), reverse=True)
    return rows[:limit]
