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
from lib.pwa import add_to_home_screen

materialize_secrets()
user = require_login()
logout_button()
add_to_home_screen("X投稿チェック")   # スマホの「ホーム画面に追加」でアプリ風に開ける

SNS_SPREADSHEET_ID = "1jqpjM7bujJVm9uh7Hz85nvSWZdFFWp942mMltXBC_T8"  # 「SNS集客」
WS_NAME = "x_posts"


@st.cache_resource(show_spinner=False)
def _ws():
    return get_client().open_by_key(SNS_SPREADSHEET_ID).worksheet(WS_NAME)


@st.cache_data(ttl=60, show_spinner=False)
def _all_values():
    """シート全体を最大60秒キャッシュ（再描画のたびに読まないfor API節約）。"""
    return _ws().get_all_values()


import re


def _img_view(url):
    """Google Drive の共有URLを <img> で表示できるサムネイル形式に変換（軽量な外部URLのまま渡す）。
    ★以前 data URI で埋め込んだら、画像が大きすぎてコンポーネントへ渡すデータが膨らみ
      『not connected to a server』でスワイプUIが落ちた。img の src に外部URLを入れる分には
      クロスオリジンでも“表示”はできる（CORSはcanvas読み出しのみ制限）ので、URLのまま渡す。"""
    if not url:
        return ""
    m = re.search(r"(?:id=|/d/)([\w-]{20,})", url)
    if m:
        # lh3 直リンク（200を直接返す）。drive.google.com/thumbnail は302リダイレクト方式で
        # iframe内だと失敗→onerrorで非表示に固定されることがあるため、直リンクに変更。
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
            drafts.append({"row": rnum, "id": g("id"),
                           "category": g("category") or "投稿", "draft": g("draft"),
                           "img": _img_view(g("image_url"))})
        elif g("status") == "approved":
            queued += 1
    # 日利は重要度が高いので常にチェックの先頭に（category=日利 or id=x-nichiri を優先）
    def _is_nichiri(d):
        return d["category"] == "日利" or str(d["id"]).startswith("x-nichiri")
    drafts.sort(key=lambda d: 0 if _is_nichiri(d) else 1)
    return drafts, queued, idx


def apply_decisions(idx, id2info, decisions, edits):
    """decisions: {id:'a'|'s'} を status列へ、edits: {id:本文} を draft列へ一括書込み（API 1回）。
    編集は元の本文と変わっていて、かつ廃棄でない場合のみ反映する。"""
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
            # ✏️編集して採用＝「こう書きたい」見本。生成が文体を寄せるための印を付ける
            if src_col:
                cells.append(gspread.Cell(r, src_col, "✏️編集採用(見本)"))
            edited += 1
        adopted += act == "a"
        skipped += act == "s"
    if cells:
        _ws().update_cells(cells)
    return adopted, skipped, edited


st.title("📮 X投稿チェック")

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
    """ドラフトに実写真を添付する。写真→Drive公開URL化→該当行のimage_urlに書込み。
    Threads公式APIは公開URLの画像しか貼れないため、Driveで公開URLにしてから紐づける。"""
    if idx.get("image_url", -1) < 0:
        return  # image_url列が無いシートでは出さない
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
                _all_values.clear()  # キャッシュ破棄→カードに反映
                st.success("写真を添付しました。下のカードに反映されます。")
                st.rerun()
            except Exception as e:
                st.error(f"アップロードに失敗しました：{e}")


_attach_image_ui()

cards = [{"id": d["id"], "cat": d["category"], "text": d["draft"], "img": d.get("img", "")}
         for d in drafts]
result = swipe_cards(cards=cards, default=None)

if result and isinstance(result, dict) and result.get("nonce") != st.session_state.get("swipe_nonce"):
    st.session_state["swipe_nonce"] = result.get("nonce")
    id2info = {d["id"]: d for d in drafts}
    a, s, e = apply_decisions(idx, id2info, result.get("decisions", {}), result.get("edits", {}))
    edited_note = f"／✏️ 編集反映 {e}件" if e else ""
    st.session_state["swipe_msg"] = f"✅ 採用 {a}件（ドリップに追加）／🗑 廃棄 {s}件{edited_note}"
    _all_values.clear()  # キャッシュ破棄→次回は最新を読む
    st.rerun()
