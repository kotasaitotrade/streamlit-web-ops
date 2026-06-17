"""実行履歴ページ。自分のジョブのみ表示。admin role なら全件表示。"""
import pandas as pd
import streamlit as st

from lib.sheets import materialize_secrets, list_jobs_for_user, list_all_jobs
from lib.auth import require_login, logout_button

st.set_page_config(page_title="実行履歴", layout="wide", page_icon="📋")
materialize_secrets()

user = require_login()
logout_button()

st.title("📋 実行履歴")

is_admin = str(user.get("role", "")).strip().lower() == "admin"
show_all = is_admin and st.toggle("全ユーザー分を表示（admin）", value=False)

if st.button("🔄 更新"):
    st.rerun()

if show_all:
    jobs = list_all_jobs(limit=200)
else:
    jobs = list_jobs_for_user(user["email"], limit=100)

if not jobs:
    st.info("ジョブはまだありません。")
    st.stop()

df = pd.DataFrame(jobs)
# アイコン（色）＋日本語ラベルで状態を表示（色だけに依存しない）
status_label = {
    "queued":   "🟡 待機中",
    "running":  "🔵 実行中",
    "success":  "🟢 成功",
    "failed":   "🔴 失敗",
    "canceled": "⚪ キャンセル",
}
df["状態"] = df["status"].map(lambda s: status_label.get(s, f"⚪ {s}"))

# 日時を読みやすい形式（YYYY-MM-DD HH:MM）に整形
for col in ["requested_at", "started_at", "finished_at"]:
    if col in df.columns:
        df[col] = (
            pd.to_datetime(df[col], errors="coerce")
            .dt.strftime("%Y-%m-%d %H:%M")
            .fillna("")
        )

display_cols = [c for c in ["状態", "tool_name", "requested_at", "started_at", "finished_at", "log_url", "error", "user_email"] if c in df.columns]
if not show_all:
    display_cols = [c for c in display_cols if c != "user_email"]

st.dataframe(
    df[display_cols],
    hide_index=True,
    use_container_width=True,
    column_config={
        "tool_name":   st.column_config.TextColumn("ツール"),
        "requested_at": st.column_config.TextColumn("受付日時"),
        "started_at":  st.column_config.TextColumn("開始日時"),
        "finished_at": st.column_config.TextColumn("完了日時"),
        "error":       st.column_config.TextColumn("エラー"),
        "user_email":  st.column_config.TextColumn("ユーザー"),
        "log_url":     st.column_config.LinkColumn("ログ"),
    },
)
