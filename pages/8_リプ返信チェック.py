"""リプ返信チェックページ（スマホ対応）。

x_replies（リプ監視・リプ営業が作った返信案）を確認し、選んだものに「返信を依頼」する。
★実際の返信投稿は、Xの認証を持つローカルのワーカー(x_post_requests_worker)が
　post_request を検知して in_reply_to 付きで投稿する（この画面はXへ直接投稿しない）。
自動返信はしない＝必ず人のチェックを挟む。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import streamlit as st

from lib.sheets import get_client, materialize_secrets
from lib.auth import require_login, logout_button

materialize_secrets()
user = require_login()
logout_button()

JST = timezone(timedelta(hours=9))
SNS_SPREADSHEET_ID = "1jqpjM7bujJVm9uh7Hz85nvSWZdFFWp942mMltXBC_T8"  # 「SNS集客」
WS_NAME = "x_replies"


def _wl(text: str) -> int:
    return sum(2 if ord(c) > 0x2000 else 1 for c in text)


SRC_LABEL = {"mention": "💬 自分宛リプ", "hunter": "🗣 リプ営業"}

st.title("💬 リプ返信チェック")
st.caption("返信案を確認して「返信を依頼」すると、数分以内にXへ返信されます（投稿はサーバー側が実行）。")


@st.cache_resource(show_spinner=False)
def _ws():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


@st.cache_data(ttl=60, show_spinner=False)
def _all_values():
    """シート全体を最大60秒キャッシュ（API読み取りの節約）。"""
    return _ws().get_all_values()


def load_queue():
    ws = _ws()
    vals = _all_values()
    if not vals:
        return ws, [], {}
    h = vals[0]
    idx = {name: (h.index(name) if name in h else -1) for name in
           ("id", "source", "author", "target_id", "target_url", "target_text",
            "draft", "status", "tweet_id", "post_request")}
    rows = []
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if not g("tweet_id") and g("draft").strip():
            rows.append({"row": rnum, "id": g("id"), "source": g("source"),
                         "author": g("author"), "target_url": g("target_url"),
                         "target_text": g("target_text"), "draft": g("draft"),
                         "requested": bool(g("post_request").strip())})
    return ws, rows, idx


ws, queue, idx = load_queue()

if not queue:
    st.success("未対応の返信案はありません。")
    st.stop()

waiting = sum(1 for q in queue if q["requested"])
if waiting:
    st.info(f"⏳ 返信依頼済み（まもなく投稿）：{waiting}件")

st.divider()

for q in queue:
    with st.container(border=True):
        head = SRC_LABEL.get(q["source"], q["source"]) + f"　@{q['author']}"
        st.checkbox(f"**{head}** に返信する" + ("　⏳依頼済み" if q["requested"] else ""),
                    key=f"rchk_{q['id']}", disabled=q["requested"])
        st.caption("相手の投稿：")
        st.markdown(f"> {q['target_text'][:200]}")
        if q["target_url"]:
            st.caption(f"🔗 {q['target_url']}")
        st.text_area("返信文（送信前に編集できます）", value=q["draft"],
                     key=f"rtxt_{q['id']}", height=120, label_visibility="collapsed",
                     disabled=q["requested"])
        wl = _wl(st.session_state.get(f"rtxt_{q['id']}", q["draft"]))
        st.caption(("⚠️ " if wl > 280 else "") + f"文字数 {wl}/280")

st.divider()

selected = [q for q in queue if st.session_state.get(f"rchk_{q['id']}") and not q["requested"]]
st.markdown(f"### 選択中：{len(selected)}件")
confirm = st.checkbox("内容を確認しました（この内容で返信を依頼する）", key="confirm_reply")
disabled = (len(selected) == 0) or (not confirm)

if st.button(f"💬 選択した{len(selected)}件の返信を依頼", type="primary",
             use_container_width=True, disabled=disabled):
    import gspread
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    cells, ok = [], 0
    for q in selected:
        text = st.session_state.get(f"rtxt_{q['id']}", q["draft"]).strip()
        if _wl(text) > 280:
            st.error(f"❌ 文字数超過のため除外：{text[:24]}…")
            continue
        if text != q["draft"]:
            cells.append(gspread.Cell(q["row"], idx["draft"] + 1, text))
        cells.append(gspread.Cell(q["row"], idx["post_request"] + 1, now))
        ok += 1
    if cells:
        ws.update_cells(cells)  # まとめて1回で書込み
    st.success(f"✅ {ok}件の返信を依頼しました。数分以内にXへ返信されます。")
    _all_values.clear()
    st.button("🔄 一覧を更新", on_click=st.rerun)
