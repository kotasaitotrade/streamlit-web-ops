"""フォローチェックページ（承認制の自動フォロー）。

x_follows（フォロー候補ファインダーが集めたアカウント）を一覧表示。
各アカウントのリンク・フォロワー数・1日の投稿数・リプ数を見て、1件ずつ「フォローを依頼」する。
★実際のフォローは、Xの認証を持つローカルのワーカー(x_follow_worker)が
　follow_request を検知してブラウザでフォローする（この画面はXへ直接操作しない）。
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
WS_NAME = "x_follows"

st.title("➕ フォローチェック")
st.caption("候補アカウントを確認して「フォローを依頼」を押すと、数分以内にフォローします（実行はサーバー側）。")


@st.cache_resource(show_spinner=False)
def _ws_follows():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


@st.cache_data(ttl=60, show_spinner=False)
def _all_values_follows():
    return _ws_follows().get_all_values()


def _int(s):
    try:
        return int(str(s).replace(",", "").strip() or 0)
    except Exception:
        return 0


def load_queue():
    ws = _ws_follows()
    vals = _all_values_follows()
    if not vals:
        return ws, [], {}
    h = vals[0]
    idx = {name: (h.index(name) if name in h else -1) for name in
           ("id", "created", "handle", "url", "followers", "posts_per_day",
            "replies_per_day", "bio", "status", "follow_request", "followed_at")}
    rows = []
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("status") in ("skip", "followed", "error") or g("followed_at").strip():
            continue
        if not g("handle").strip():
            continue
        rows.append({"row": rnum, "id": g("id"), "created": g("created"),
                     "handle": g("handle"), "url": g("url") or f"https://x.com/{g('handle')}",
                     "followers": _int(g("followers")), "posts": _int(g("posts_per_day")),
                     "replies": _int(g("replies_per_day")), "bio": g("bio"),
                     "requested": bool(g("follow_request").strip())})
    # フォロワー数の多い順（リーチ期待）
    rows.sort(key=lambda q: q["followers"], reverse=True)
    return ws, rows, idx


ws, queue, idx = load_queue()

if not queue:
    st.success("未対応のフォロー候補はありません。")
    st.stop()

waiting = sum(1 for q in queue if q["requested"])
if waiting:
    st.info(f"⏳ フォロー依頼済み（まもなく実行）：{waiting}件")
st.caption(f"未対応：{len(queue) - waiting}件")
st.divider()


def _request_follow(q):
    import gspread
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    ws.update_cell(q["row"], idx["follow_request"] + 1, now)
    _all_values_follows.clear()


def _skip(q):
    ws.update_cell(q["row"], idx["status"] + 1, "skip")
    _all_values_follows.clear()


for q in queue:
    with st.container(border=True):
        st.markdown(f"### [@{q['handle']}]({q['url']})"
                    + ("　⏳ 依頼済み" if q["requested"] else ""))
        c1, c2, c3 = st.columns(3)
        c1.metric("フォロワー", f"{q['followers']:,}")
        c2.metric("投稿/日", q["posts"])
        c3.metric("リプ/日", q["replies"])
        if q["bio"]:
            st.caption(q["bio"])
        st.markdown(f"[🔗 プロフィールを開く]({q['url']})")
        if not q["requested"]:
            b1, b2 = st.columns([2, 1])
            if b1.button("➕ フォローを依頼", key=f"f_{q['id']}", type="primary",
                         use_container_width=True):
                _request_follow(q)
                st.success(f"✅ @{q['handle']} をフォロー依頼しました")
                st.rerun()
            if b2.button("🗑 見送り", key=f"s_{q['id']}", use_container_width=True):
                _skip(q)
                st.rerun()
