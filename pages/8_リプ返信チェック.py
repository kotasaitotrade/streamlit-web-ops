"""リプ返信チェックページ（スマホ対応）。

x_replies（リプ営業ファインダーが作った返信案）を確認し、1件ずつ「返信を依頼」する。
★実際の返信投稿は、Xの認証を持つローカルのワーカー(x_post_requests_worker)が
　post_request を検知してブラウザ返信で投稿する（この画面はXへ直接投稿しない）。
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


SRC_LABEL = {"mention": "💬 自分宛リプ", "hunter": "🗣 リプ営業",
             "finder_browser": "🗣 リプ営業"}

st.title("💬 リプ返信チェック")
st.caption("各リプの「返信を依頼」を押すと、その1件が数分以内にXへ返信されます（投稿はサーバー側が実行）。")


@st.cache_resource(show_spinner=False)
def _ws():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


@st.cache_data(ttl=60, show_spinner=False)
def _all_values():
    return _ws().get_all_values()


def load_queue():
    ws = _ws()
    vals = _all_values()
    if not vals:
        return ws, [], {}
    h = vals[0]
    idx = {name: (h.index(name) if name in h else -1) for name in
           ("id", "created", "source", "author", "target_id", "target_url", "target_text",
            "draft", "status", "tweet_id", "post_request")}
    rows = []
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("status") in ("expired", "skip") or g("tweet_id"):
            continue
        if g("draft").strip():
            rows.append({"row": rnum, "id": g("id"), "created": g("created"),
                         "source": g("source"), "author": g("author"),
                         "target_url": g("target_url"), "target_text": g("target_text"),
                         "draft": g("draft"), "requested": bool(g("post_request").strip())})
    # 新しい候補（created降順）を上に
    rows.sort(key=lambda q: q["created"], reverse=True)
    return ws, rows, idx


ws, queue, idx = load_queue()

if not queue:
    st.success("未対応の返信案はありません。")
    st.stop()

waiting = sum(1 for q in queue if q["requested"])
if waiting:
    st.info(f"⏳ 返信依頼済み（まもなく投稿）：{waiting}件")
st.caption(f"未対応：{len(queue) - waiting}件")
st.divider()


def _request_reply(q, text):
    """1件だけ返信を依頼（post_requestを立てる）。編集本文も反映。"""
    import gspread
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    cells = [gspread.Cell(q["row"], idx["post_request"] + 1, now)]
    if text.strip() and text.strip() != q["draft"]:
        cells.append(gspread.Cell(q["row"], idx["draft"] + 1, text.strip()))
    ws.update_cells(cells)
    _all_values.clear()


def _skip_reply(q):
    ws.update_cell(q["row"], idx["status"] + 1, "skip")
    _all_values.clear()


for q in queue:
    with st.container(border=True):
        head = SRC_LABEL.get(q["source"], q["source"]) + f"　@{q['author']}"
        st.markdown(f"**{head}**" + ("　⏳ 依頼済み" if q["requested"] else ""))
        if q["created"]:
            st.caption(f"🕒 投稿日時（候補取得）: {q['created']}")
        st.caption("相手の投稿：")
        st.markdown(f"> {q['target_text'][:200]}")
        if q["target_url"]:
            st.markdown(f"[🔗 元ツイートを開く]({q['target_url']})")
        text = st.text_area("返信文（送信前に編集できます）", value=q["draft"],
                            key=f"rtxt_{q['id']}", height=120,
                            label_visibility="collapsed", disabled=q["requested"])
        wl = _wl(text or "")
        st.caption(("⚠️ " if wl > 280 else "") + f"文字数 {wl}/280")
        if not q["requested"]:
            c1, c2 = st.columns([2, 1])
            if c1.button("💬 このリプを依頼", key=f"req_{q['id']}", type="primary",
                         use_container_width=True):
                if _wl(text) > 280:
                    st.error("文字数が280を超えています。短くしてください。")
                else:
                    _request_reply(q, text)
                    st.success(f"✅ @{q['author']} に返信を依頼しました")
                    st.rerun()
            if c2.button("🗑 見送り", key=f"skip_{q['id']}", use_container_width=True):
                _skip_reply(q)
                st.rerun()
