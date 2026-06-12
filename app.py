"""Web Ops メインエントリ。ログイン → ホーム。"""
import streamlit as st

from lib.sheets import materialize_secrets, ensure_sheets_initialized, list_tools, user_allowed_tools
from lib.auth import require_login, logout_button

st.set_page_config(page_title="Web Ops", layout="wide", page_icon="🛠")

materialize_secrets()

# 起動時に1回だけシート初期化チェック（admin が触らなくても自動）
if "sheets_initialized" not in st.session_state:
    try:
        created = ensure_sheets_initialized()
        if created:
            st.toast(f"初期シートを作成しました: {', '.join(created)}", icon="✅")
    except Exception as e:
        st.warning(f"シート初期化に失敗: {e}")
    st.session_state["sheets_initialized"] = True

user = require_login()
logout_button()

st.title("🛠 Web Ops")
st.write(f"こんにちは、**{user.get('display_name') or user.get('email')}** さん")

all_tools = list_tools()
all_tool_names = [t["tool_name"] for t in all_tools]
allowed = user_allowed_tools(user, all_tool_names)
my_tools = [t for t in all_tools if t["tool_name"] in allowed]

if not my_tools:
    st.info("実行可能なツールがありません。管理者に権限付与を依頼してください。")
else:
    st.subheader(f"使えるツール: {len(my_tools)}")
    cols = st.columns(min(3, len(my_tools)))
    for i, t in enumerate(my_tools):
        with cols[i % len(cols)]:
            with st.container(border=True):
                st.markdown(f"### {t.get('display_name') or t['tool_name']}")
                if t.get("description"):
                    st.caption(t["description"])
                st.page_link("pages/1_ツール実行.py", label="▶ 実行する", icon="🚀")

st.divider()
st.subheader("📦 Amazon出品管理")
with st.container(border=True):
    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.markdown("**ASIN自動取得 / 自動出品 / 価格調整 / FNSKUラベル生成**")
        st.caption("SP-API + Google Drive 連携。ドライランで安全確認後に本番実行できます。")
    with col_b:
        st.page_link("pages/3_Amazon出品管理.py", label="▶ 開く", icon="📦", use_container_width=True)

st.divider()
st.page_link("pages/2_実行履歴.py", label="📋 実行履歴を見る")
