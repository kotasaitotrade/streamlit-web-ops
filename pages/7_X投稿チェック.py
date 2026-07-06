"""X投稿チェックページ（スマホ対応）。

生成された投稿ネタ（x_posts の status=draft）を確認し、選んだものを「ドリップに追加」する。
★すぐには投稿しない。追加した分は status=approved になり、ドリップ(1日約10件・時間バラけ)で
　順次Xへ投稿される（実際の投稿はXの認証を持つローカルのドリップ処理が実行）。
画像プレビューは Drive の公開URL(image_url)で表示する。
"""
from __future__ import annotations

import requests
import streamlit as st

from lib.sheets import get_client, materialize_secrets
from lib.auth import require_login, logout_button

materialize_secrets()
user = require_login()
logout_button()

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
st.caption("投稿ネタを確認して「ドリップに追加」すると、すぐには投稿されず、1日約10件・時間をばらして順次Xへ投稿されます。")


@st.cache_resource(show_spinner=False)
def _ws():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


def load_data():
    ws = _ws()
    vals = ws.get_all_values()
    if not vals:
        return ws, [], 0, {}
    h = vals[0]
    idx = {name: (h.index(name) if name in h else -1) for name in
           ("id", "category", "type", "draft", "status", "tweet_id", "image_url")}
    drafts, queued = [], 0
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("type") != "original" or g("tweet_id"):
            continue
        if g("status") == "draft":
            drafts.append({"row": rnum, "id": g("id"), "category": g("category") or "投稿",
                           "draft": g("draft"), "image_url": g("image_url")})
        elif g("status") == "approved":
            queued += 1
    return ws, drafts, queued, idx


ws, drafts, queued, idx = load_data()

if queued:
    st.info(f"🟢 ドリップ待ち（投稿予約済み）：{queued}件")

if not drafts:
    st.success("チェック待ちの投稿ネタはありません。")
    st.stop()

c1, c2 = st.columns(2)
if c1.button("✅ すべて選択", use_container_width=True):
    for q in drafts:
        st.session_state[f"chk_{q['id']}"] = True
if c2.button("⬜ すべて解除", use_container_width=True):
    for q in drafts:
        st.session_state[f"chk_{q['id']}"] = False

st.divider()

for q in drafts:
    with st.container(border=True):
        st.checkbox(f"**【{q['category']}】** をドリップに追加", key=f"chk_{q['id']}")
        st.text_area("本文（追加前に編集できます）", value=q["draft"],
                     key=f"txt_{q['id']}", height=140, label_visibility="collapsed")
        wl = _wl(st.session_state.get(f"txt_{q['id']}", q["draft"]))
        st.caption(("⚠️ " if wl > 280 else "") + f"文字数 {wl}/280" +
                   ("　🖼️画像あり" if q["image_url"] else ""))
        if q["image_url"]:
            b = _img_bytes(q["image_url"])
            if b:
                st.image(b, use_container_width=True)

st.divider()

selected = [q for q in drafts if st.session_state.get(f"chk_{q['id']}")]
st.markdown(f"### 選択中：{len(selected)}件")
c3, c4 = st.columns(2)
with c4:
    if st.button(f"🗑 選択{len(selected)}件を見送り(skip)", use_container_width=True,
                 disabled=len(selected) == 0):
        for q in selected:
            ws.update_cell(q["row"], idx["status"] + 1, "skip")
        st.success(f"{len(selected)}件を見送りにしました。")
        _ws.clear()
        st.button("🔄 更新", on_click=st.rerun)
with c3:
    if st.button(f"📥 選択{len(selected)}件をドリップに追加", type="primary",
                 use_container_width=True, disabled=len(selected) == 0):
        ok = 0
        for q in selected:
            text = st.session_state.get(f"txt_{q['id']}", q["draft"]).strip()
            if _wl(text) > 280:
                st.error(f"❌ 文字数超過のため除外：{text[:24]}…")
                continue
            if text != q["draft"]:
                ws.update_cell(q["row"], idx["draft"] + 1, text)
            ws.update_cell(q["row"], idx["status"] + 1, "approved")  # ドリップ対象に
            ok += 1
        st.success(f"✅ {ok}件をドリップに追加しました。1日約10件・時間をばらして順次投稿されます。")
        _ws.clear()
        st.button("🔄 一覧を更新", on_click=st.rerun)
