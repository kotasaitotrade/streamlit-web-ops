"""X投稿チェック（2）ページ — ちー垢用の独立チェックスペース。

こうたの「X投稿チェック」(pages/7)とは別アカウント運用のため、読む台帳タブを
x_posts_chii に分離した独立ページ。UI・操作は pages/7 と同一（スワイプで採用/廃棄）。
role=x_reviewer のユーザー（ちー・koutaiwi）はこのページのみが表示される（app.py で制御）。
"""
from __future__ import annotations

import streamlit as st

# ★このページの<title>を「ちーさん」に（iOS/Androidの「ホーム画面に追加」名はこれを使う）。
st.set_page_config(page_title="ちーさん", page_icon="📮", layout="wide")

from lib.sheets import get_client, materialize_secrets
from lib.auth import require_login, logout_button
from lib.swipe import swipe_cards
from lib.pwa import add_to_home_screen

materialize_secrets()
user = require_login()
logout_button()
add_to_home_screen("ちーさん")   # スマホの「ホーム画面に追加」でアプリ風に開ける

SNS_SPREADSHEET_ID = "1jqpjM7bujJVm9uh7Hz85nvSWZdFFWp942mMltXBC_T8"  # 「SNS集客」
WS_NAME = "x_posts_chii"   # ★ちー垢用の別台帳タブ（こうたの x_posts とは分離）


@st.cache_resource(show_spinner=False)
def _ws():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


@st.cache_data(ttl=60, show_spinner=False)
def _all_values():
    """シート全体を最大60秒キャッシュ（再描画のたびに読まないfor API節約）。"""
    return _ws().get_all_values()


import re


def _img_view(url):
    """Google Drive の共有URLを <img> で表示できるサムネイル形式に変換（軽量な外部URLのまま渡す）。"""
    if not url:
        return ""
    m = re.search(r"(?:id=|/d/)([\w-]{20,})", url)
    if m:
        return f"https://lh3.googleusercontent.com/d/{m.group(1)}=w640"
    return url


def load_data():
    vals = _all_values()
    if not vals:
        return [], 0, {}
    h = vals[0]
    idx = {name: (h.index(name) if name in h else -1) for name in
           ("id", "category", "type", "draft", "status", "tweet_id", "image_url", "source")}
    drafts, queued = [], 0
    for rnum, r in enumerate(vals[1:], start=2):
        g = lambda name: (r[idx[name]] if 0 <= idx[name] < len(r) else "")
        if g("type") != "original" or g("tweet_id"):
            continue
        if g("status") == "draft":
            _imgs = [_img_view(u) for u in g("image_url").split("|") if u.strip()]
            drafts.append({"row": rnum, "id": g("id"),
                           "category": g("category") or "投稿", "draft": g("draft"),
                           "img": _imgs[0] if _imgs else "", "imgs": _imgs})
        elif g("status") == "approved":
            queued += 1
    def _is_nichiri(d):
        return d["category"] == "日利" or str(d["id"]).startswith("x-nichiri")
    drafts.sort(key=lambda d: 0 if _is_nichiri(d) else 1)
    return drafts, queued, idx


def apply_decisions(idx, id2info, decisions, edits):
    """decisions: {id:'a'|'s'} を status列へ、edits: {id:本文} を draft列へ一括書込み（API 1回）。"""
    import gspread
    st_col = idx["status"] + 1
    dr_col = idx["draft"] + 1
    src_col = idx["source"] + 1 if idx.get("source", -1) >= 0 else 0
    cells, adopted, skipped, edited = [], 0, 0, 0
    for pid, act in decisions.items():
        info = id2info.get(pid)
        if not info:
            continue
        r = info["row"]
        cells.append(gspread.Cell(r, st_col, "approved" if act == "a" else "skip"))
        new = (edits or {}).get(pid)
        if act == "a" and new is not None and new.strip() and new != info["draft"]:
            cells.append(gspread.Cell(r, dr_col, new))
            if src_col:
                cells.append(gspread.Cell(r, src_col, "✏️編集採用(見本)"))
            edited += 1
        adopted += act == "a"
        skipped += act == "s"
    if cells:
        _ws().update_cells(cells)
    return adopted, skipped, edited


st.title("📮 ちーさん")

msg = st.session_state.pop("swipe_msg", None)
if msg:
    st.success(msg)

st.caption("右スワイプ＝採用（ドリップに追加）／左スワイプ＝廃棄。✏️で本文を編集できます。採用分は1日約10件・時間をばらして順次投稿されます。")

drafts, queued, idx = load_data()
if queued:
    st.info(f"🟢 ドリップ待ち（投稿予約済み）：{queued}件")

if not drafts:
    st.success("チェック待ちの投稿ネタはありません。")
    st.stop()


def _attach_image_ui():
    """ドラフトに実写真を添付する。写真→Drive公開URL化→該当行のimage_urlに書込み。"""
    if idx.get("image_url", -1) < 0:
        return
    with st.expander("📷 写真を添付（実物・本人撮影のみ）"):
        no_img = [d for d in drafts if not d.get("img")]
        if not no_img:
            st.caption("画像未設定のドラフトはありません。")
            return
        opt = {f'{d["category"]}｜{d["draft"][:24]}…': d for d in no_img}
        label = st.selectbox("写真をつけるドラフト", list(opt.keys()))
        up = st.file_uploader("写真を選ぶ（JPG / PNG）", type=["jpg", "jpeg", "png"])
        if up is not None:
            st.image(up, width=240, caption="プレビュー")
        if st.button("アップロードして添付", disabled=(up is None)):
            from lib.drive import upload_image_public
            d = opt[label]
            try:
                with st.spinner("アップロード中…"):
                    url = upload_image_public(up.name, up.getvalue())
                    _ws().update_cell(d["row"], idx["image_url"] + 1, url)
                _all_values.clear()
                st.success("写真を添付しました。下のカードに反映されます。")
                st.rerun()
            except Exception as e:
                st.error(f"アップロードに失敗しました：{e}")


_attach_image_ui()

cards = [{"id": d["id"], "cat": d["category"], "text": d["draft"],
          "img": d.get("img", ""), "imgs": d.get("imgs", [])}
         for d in drafts]
result = swipe_cards(cards=cards, default=None)

if result and isinstance(result, dict) and result.get("nonce") != st.session_state.get("swipe_nonce"):
    st.session_state["swipe_nonce"] = result.get("nonce")
    id2info = {d["id"]: d for d in drafts}
    a, s, e = apply_decisions(idx, id2info, result.get("decisions", {}), result.get("edits", {}))
    edited_note = f"／✏️ 編集反映 {e}件" if e else ""
    st.session_state["swipe_msg"] = f"✅ 採用 {a}件（ドリップに追加）／🗑 廃棄 {s}件{edited_note}"
    _all_values.clear()
    st.rerun()
