"""X投稿チェックページ（スマホ対応）。

ドリップ待ち（x_posts の status=approved・未投稿）の投稿をチェックして、選んだものを
その場でXへ投稿できる。投稿すると status=posted・tweet_id・投稿日を書き戻す。
自動ドリップと同じ在庫を、人が手動で精査→投稿するための画面。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import streamlit as st

from lib.sheets import get_client, materialize_secrets
from lib.auth import require_login, logout_button
import lib.x_post as xp

materialize_secrets()
user = require_login()
logout_button()

JST = timezone(timedelta(hours=9))
SNS_SPREADSHEET_ID = "1jqpjM7bujJVm9uh7Hz85nvSWZdFFWp942mMltXBC_T8"  # 「SNS集客」
WS_NAME = "x_posts"

st.title("📮 X投稿チェック")
st.caption("ドリップ待ちの投稿を確認して、選んだものをXに投稿します。スマホでもそのまま使えます。")


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
           ("id", "category", "type", "draft", "status", "tweet_id", "image", "date")}
    rows = []
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("type") == "original" and g("status") == "approved" and not g("tweet_id"):
            rows.append({"row": rnum, "id": g("id"), "category": g("category") or "投稿",
                         "draft": g("draft"), "image": g("image")})
    return ws, rows, idx


if not xp.has_credentials():
    st.error("X APIの認証情報が未設定です。secrets に `[x_api]`（consumer_key / consumer_secret / "
             "access_token / access_token_secret）を追加してください。")
    st.stop()

ws, queue, idx = load_queue()

if not queue:
    st.success("ドリップ待ちの投稿はありません（すべて投稿済み or 承認待ちなし）。")
    st.stop()

# 全選択トグル（スマホで押しやすいよう上部に）
top = st.container()
with top:
    c1, c2 = st.columns(2)
    if c1.button("✅ すべて選択", use_container_width=True):
        for q in queue:
            st.session_state[f"chk_{q['id']}"] = True
    if c2.button("⬜ すべて解除", use_container_width=True):
        for q in queue:
            st.session_state[f"chk_{q['id']}"] = False

st.divider()

# 各投稿を縦1列カードで（スマホ最適）。チェック＋本文編集＋画像プレビュー
for q in queue:
    with st.container(border=True):
        checked = st.checkbox(f"**【{q['category']}】** を投稿する", key=f"chk_{q['id']}")
        text = st.text_area("本文（送信前に編集できます）", value=q["draft"],
                            key=f"txt_{q['id']}", height=140, label_visibility="collapsed")
        wl = xp.weighted_len(text)
        over = wl > 280
        st.caption(("⚠️ " if over else "") + f"文字数 {wl}/280" + ("（超過。短くしてください）" if over else "") +
                   ("　🖼️画像あり" if xp.resolve_image(q["image"]) else "　（画像なし）"))
        img = xp.resolve_image(q["image"])
        if img and checked:
            st.image(img, use_container_width=True)

st.divider()

selected = [q for q in queue if st.session_state.get(f"chk_{q['id']}")]
st.markdown(f"### 選択中：{len(selected)}件")

confirm = st.checkbox("内容を確認しました（投稿を実行してよい）", key="confirm_post")
disabled = (len(selected) == 0) or (not confirm)
if st.button(f"🚀 選択した{len(selected)}件をXに投稿", type="primary",
             use_container_width=True, disabled=disabled):
    ok, ng = 0, 0
    prog = st.progress(0.0)
    for i, q in enumerate(selected, 1):
        text = st.session_state.get(f"txt_{q['id']}", q["draft"]).strip()
        if xp.weighted_len(text) > 280:
            st.error(f"❌ 文字数超過のためスキップ：{text[:24]}…")
            ng += 1
            prog.progress(i / len(selected))
            continue
        try:
            tid = xp.post_tweet(text, q["image"])
            now = datetime.now(JST)
            ws.update_cell(q["row"], idx["status"] + 1, "posted")
            ws.update_cell(q["row"], idx["tweet_id"] + 1, tid)
            if idx["date"] >= 0:
                ws.update_cell(q["row"], idx["date"] + 1, now.strftime("%Y-%m-%d %H:%M"))
            st.success(f"✅ 投稿しました → https://x.com/revolt_shop_01/status/{tid}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            st.error(f"❌ 投稿失敗：{str(e)[:160]}")
            ng += 1
        prog.progress(i / len(selected))
    st.info(f"完了：成功 {ok}件 / 失敗 {ng}件")
    _ws.clear()  # キャッシュ更新
    st.button("🔄 一覧を更新", on_click=lambda: st.rerun())
