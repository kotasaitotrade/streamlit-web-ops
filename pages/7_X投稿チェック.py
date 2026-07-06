"""X投稿チェックページ（スマホ対応）。

ドリップ待ち（x_posts の status=approved・未投稿）を確認し、選んだものに「投稿を依頼」する。
★Xへの実際の投稿は、Xの認証を持つローカル環境のワーカー(src/x_post_requests_worker.py)が
　post_request フラグを検知して実行する（この画面はXへ直接投稿しない＝Xシークレット不要）。
画像プレビューは Drive の公開URL(image_url)で表示する。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import requests
import streamlit as st

from lib.sheets import get_client, materialize_secrets
from lib.auth import require_login, logout_button

materialize_secrets()
user = require_login()
logout_button()

JST = timezone(timedelta(hours=9))
SNS_SPREADSHEET_ID = "1jqpjM7bujJVm9uh7Hz85nvSWZdFFWp942mMltXBC_T8"  # 「SNS集客」
WS_NAME = "x_posts"


def _wl(text: str) -> int:
    return sum(2 if ord(c) > 0x2000 else 1 for c in text)


@st.cache_data(show_spinner=False, ttl=3600)
def _img_bytes(url: str):
    """Drive公開URLはブラウザの<img>で直接表示できないため、サーバー側で取得してバイトを返す。"""
    try:
        r = requests.get(url, timeout=20, allow_redirects=True)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
    except Exception:
        pass
    return None


st.title("📮 X投稿チェック")
st.caption("ドリップ待ちを確認して「投稿を依頼」すると、数分以内にXへ投稿されます（投稿はサーバー側が実行）。")


@st.cache_resource(show_spinner=False)
def _ws():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


def load_queue():
    ws = _ws()
    vals = ws.get_all_values()
    if not vals:
        return ws, [], {}
    h = vals[0]
    idx = {name: (h.index(name) if name in h else -1) for name in
           ("id", "category", "type", "draft", "status", "tweet_id", "image_url", "post_request")}
    rows = []
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("type") == "original" and g("status") == "approved" and not g("tweet_id"):
            rows.append({"row": rnum, "id": g("id"), "category": g("category") or "投稿",
                         "draft": g("draft"), "image_url": g("image_url"),
                         "requested": bool(g("post_request").strip())})
    return ws, rows, idx


ws, queue, idx = load_queue()

if not queue:
    st.success("ドリップ待ちの投稿はありません。")
    st.stop()

waiting = sum(1 for q in queue if q["requested"])
if waiting:
    st.info(f"⏳ 投稿依頼済み（まもなく投稿）：{waiting}件")

c1, c2 = st.columns(2)
if c1.button("✅ すべて選択", use_container_width=True):
    for q in queue:
        st.session_state[f"chk_{q['id']}"] = True
if c2.button("⬜ すべて解除", use_container_width=True):
    for q in queue:
        st.session_state[f"chk_{q['id']}"] = False

st.divider()

for q in queue:
    with st.container(border=True):
        label = f"**【{q['category']}】** を投稿する" + ("　⏳依頼済み" if q["requested"] else "")
        st.checkbox(label, key=f"chk_{q['id']}", disabled=q["requested"])
        st.text_area("本文（送信前に編集できます）", value=q["draft"],
                     key=f"txt_{q['id']}", height=140, label_visibility="collapsed",
                     disabled=q["requested"])
        wl = _wl(st.session_state.get(f"txt_{q['id']}", q["draft"]))
        st.caption(("⚠️ " if wl > 280 else "") + f"文字数 {wl}/280" +
                   ("　🖼️画像あり" if q["image_url"] else "　（画像なし）"))
        if q["image_url"]:
            b = _img_bytes(q["image_url"])
            if b:
                st.image(b, use_container_width=True)
            else:
                st.caption("（画像プレビューを取得できませんでした）")

st.divider()

selected = [q for q in queue if st.session_state.get(f"chk_{q['id']}") and not q["requested"]]
st.markdown(f"### 選択中：{len(selected)}件")
confirm = st.checkbox("内容を確認しました（この内容で投稿を依頼する）", key="confirm_req")
disabled = (len(selected) == 0) or (not confirm)

if st.button(f"📮 選択した{len(selected)}件の投稿を依頼", type="primary",
             use_container_width=True, disabled=disabled):
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    ok = 0
    for q in selected:
        text = st.session_state.get(f"txt_{q['id']}", q["draft"]).strip()
        if _wl(text) > 280:
            st.error(f"❌ 文字数超過のため除外：{text[:24]}…")
            continue
        # 編集後の本文を保存し、投稿依頼フラグ(post_request)を立てる
        if text != q["draft"]:
            ws.update_cell(q["row"], idx["draft"] + 1, text)
        ws.update_cell(q["row"], idx["post_request"] + 1, now)
        ok += 1
    st.success(f"✅ {ok}件の投稿を依頼しました。数分以内にXへ投稿されます。")
    _ws.clear()
    st.button("🔄 一覧を更新", on_click=st.rerun)
