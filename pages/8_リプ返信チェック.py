"""リプ返信チェックページ（スマホ対応・見やすい一覧）。

x_replies（リプ営業ファインダーが作った返信案）を確認し、1件ずつ「返信を依頼」する。
・元ツイートに写真があれば表示。・元ツイートが6時間以上前のものは表示しない（鮮度切れ）。
★実際の返信投稿は、ローカルのワーカー(x_post_requests_worker)がブラウザ返信で投稿する。
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
MAX_AGE_MIN = 6 * 60   # 元ツイートがこれ以上前なら表示しない（6時間）

PERSONA = ("🧑 返信ペルソナ：**会社員SE・2児パパ**／6人の外注チーム＋自作ツールで物販を仕組み化。"
           "淡々・気合より仕組み・上から教えない。")

SRC_LABEL = {"mention": "💬 自分宛リプ", "hunter": "🗣 リプ営業", "finder_browser": "🗣 リプ営業"}


def _wl(text: str) -> int:
    return sum(2 if ord(c) > 0x2000 else 1 for c in text)


def _tweet_age_min(target_id: str):
    """target_id(snowflake)から元ツイートの経過分を算出。"""
    try:
        ms = (int(target_id) >> 22) + 1288834974657
        posted = datetime.fromtimestamp(ms / 1000, JST)
        return (datetime.now(JST) - posted).total_seconds() / 60
    except Exception:
        return None


def _age_label(mins):
    if mins is None:
        return ""
    if mins < 60:
        return f"{int(mins)}分前"
    return f"{int(mins // 60)}時間{int(mins % 60)}分前"


st.title("💬 リプ返信チェック")
st.caption(PERSONA)
st.caption("各リプの「返信を依頼」で、その1件が数分以内にXへ返信されます（投稿はサーバー側）。"
           "元ツイートが6時間以上前のものは自動で非表示。")


@st.cache_resource(show_spinner=False)
def _ws_replies():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


@st.cache_data(ttl=30, show_spinner=False)
def _all_values_replies():
    return _ws_replies().get_all_values()


def load_queue():
    ws = _ws_replies()
    vals = _all_values_replies()
    if not vals:
        return ws, [], {}
    h = vals[0]
    idx = {name: (h.index(name) if name in h else -1) for name in
           ("id", "created", "source", "author", "target_id", "target_url", "target_text",
            "target_img", "draft", "status", "tweet_id", "post_request")}
    rows = []
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("status") in ("expired", "skip") or g("tweet_id"):
            continue
        if not g("draft").strip():
            continue
        age = _tweet_age_min(g("target_id"))
        if age is not None and age > MAX_AGE_MIN:   # ★6時間超は表示しない
            continue
        # Xの投稿リンク（target_urlが空でも author/status/target_id から補完）
        url = g("target_url").strip()
        if not url and g("target_id").strip():
            url = f"https://x.com/{g('author') or 'i'}/status/{g('target_id').strip()}"
        rows.append({"row": rnum, "id": g("id"), "author": g("author"),
                     "source": g("source"), "target_url": url,
                     "target_text": g("target_text"), "target_img": g("target_img"),
                     "draft": g("draft"), "age_min": age,
                     "requested": bool(g("post_request").strip())})
    rows.sort(key=lambda q: (q["age_min"] if q["age_min"] is not None else 9e9))  # 新しい順
    return ws, rows, idx


ws, queue, idx = load_queue()

if st.button("🔄 最新を再取得", use_container_width=True):
    _all_values_replies.clear()
    st.rerun()

if not queue:
    st.success("表示できる返信案はありません（6時間以内の候補なし）。")
    st.stop()

waiting = sum(1 for q in queue if q["requested"])
st.markdown(f"#### 未対応 {len(queue) - waiting}件"
            + (f"　／　⏳ 依頼済み {waiting}件" if waiting else ""))
st.divider()


def _request_reply(q, text):
    import gspread
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    cells = [gspread.Cell(q["row"], idx["post_request"] + 1, now)]
    if text.strip() and text.strip() != q["draft"]:
        cells.append(gspread.Cell(q["row"], idx["draft"] + 1, text.strip()))
    ws.update_cells(cells)
    _all_values_replies.clear()


def _skip_reply(q):
    ws.update_cell(q["row"], idx["status"] + 1, "skip")
    _all_values_replies.clear()


for q in queue:
    with st.container(border=True):
        top = f"**{SRC_LABEL.get(q['source'], q['source'])}**　[@{q['author']}]({q['target_url']})"
        age = _age_label(q["age_min"])
        if age:
            top += f"　🕒 {age}"
        if q["requested"]:
            top += "　⏳ 依頼済み"
        st.markdown(top)
        st.markdown(f"> {q['target_text'][:220]}")
        if q["target_url"]:
            st.markdown(f"🔗 **[Xで元のポストを開く]({q['target_url']})**")
        if q["target_img"]:
            # st.image は外部URLで例外を投げることがあるので、素の<img>で安全に埋め込む
            st.markdown(
                f'<img src="{q["target_img"]}" style="max-width:280px;width:100%;'
                f'border-radius:10px;margin:4px 0;" referrerpolicy="no-referrer">',
                unsafe_allow_html=True)
        text = st.text_area("返信文（編集可）", value=q["draft"], key=f"rtxt_{q['id']}",
                            height=110, label_visibility="collapsed", disabled=q["requested"])
        wl = _wl(text or "")
        st.caption(("⚠️ " if wl > 280 else "") + f"文字数 {wl}/280")
        if not q["requested"]:
            b1, b2 = st.columns([2, 1])
            if b1.button("💬 このリプを依頼", key=f"req_{q['id']}", type="primary",
                         use_container_width=True):
                if _wl(text) > 280:
                    st.error("文字数が280を超えています。短くしてください。")
                else:
                    _request_reply(q, text)
                    st.success(f"✅ @{q['author']} に返信を依頼しました")
                    st.rerun()
            if b2.button("🗑 見送り", key=f"skip_{q['id']}", use_container_width=True):
                _skip_reply(q)
                st.rerun()
