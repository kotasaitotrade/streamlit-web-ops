"""X投稿チェックページ（スマホ・スワイプ操作）。

生成された投稿ネタ（x_posts の status=draft）をカードで表示し、
　右スワイプ＝採用（ドリップに追加＝status=approved）
　左スワイプ＝廃棄（status=skip）
で仕分ける。採用分はすぐ投稿されず、ドリップ(1日約10件・時間バラけ)で順次Xへ。
スワイプが使えない環境用に「◀廃棄 / 採用▶」タップボタンも併設。

スワイプUIは双方向カスタムコンポーネント(components/swipe_cards)で実装し、
「保存」で仕分け結果をPython側へ返す（iframeはナビゲーション不可のため）。
"""
from __future__ import annotations

import streamlit as st

from lib.sheets import get_client, materialize_secrets
from lib.auth import require_login, logout_button
from lib.swipe import swipe_cards

materialize_secrets()
user = require_login()
logout_button()

SNS_SPREADSHEET_ID = "1jqpjM7bujJVm9uh7Hz85nvSWZdFFWp942mMltXBC_T8"  # 「SNS集客」
WS_NAME = "x_posts"


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
           ("id", "category", "type", "draft", "status", "tweet_id")}
    drafts, queued = [], 0
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("type") != "original" or g("tweet_id"):
            continue
        if g("status") == "draft":
            drafts.append({"row": rnum, "id": g("id"),
                           "category": g("category") or "投稿", "draft": g("draft")})
        elif g("status") == "approved":
            queued += 1
    return ws, drafts, queued, idx


def apply_decisions(ws, idx, decisions):
    """decisions: {id: 'a'|'s'}  a=採用(approved) / s=廃棄(skip)。"""
    vals = ws.get_all_values()
    id_i, st_i = idx["id"], idx["status"]
    adopted = skipped = 0
    for rnum, r in enumerate(vals[1:], start=2):
        pid = r[id_i] if id_i < len(r) else ""
        act = decisions.get(pid)
        if act:
            ws.update_cell(rnum, st_i + 1, "approved" if act == "a" else "skip")
            adopted += act == "a"
            skipped += act == "s"
    return adopted, skipped


st.title("📮 X投稿チェック")

msg = st.session_state.pop("swipe_msg", None)
if msg:
    st.success(msg)

st.caption("右スワイプ＝採用（ドリップに追加）／左スワイプ＝廃棄。採用分は1日約10件・時間をばらして順次投稿されます。")

ws, drafts, queued, idx = load_data()
if queued:
    st.info(f"🟢 ドリップ待ち（投稿予約済み）：{queued}件")

if not drafts:
    st.success("チェック待ちの投稿ネタはありません。")
    st.stop()

cards = [{"id": d["id"], "cat": d["category"], "text": d["draft"]} for d in drafts]
result = swipe_cards(cards=cards, default=None)

if result and isinstance(result, dict) and result.get("nonce") != st.session_state.get("swipe_nonce"):
    st.session_state["swipe_nonce"] = result.get("nonce")
    a, s = apply_decisions(ws, idx, result.get("decisions", {}))
    st.session_state["swipe_msg"] = f"✅ 採用 {a}件（ドリップに追加）／🗑 廃棄 {s}件"
    _ws.clear()
    st.rerun()
