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


@st.cache_data(ttl=60, show_spinner=False)
def _all_values():
    """シート全体を最大60秒キャッシュ（再描画のたびに読まないfor API節約）。"""
    return _ws().get_all_values()


import base64
import re

import requests


def _thumb_url(url):
    """Google Drive の共有URLを、画像として取得できるサムネイル形式に変換。"""
    if not url:
        return ""
    m = re.search(r"(?:id=|/d/)([\w-]{20,})", url)
    if m:
        return f"https://drive.google.com/thumbnail?id={m.group(1)}&sz=w800"
    return url


@st.cache_data(ttl=3600, show_spinner=False)
def _img_data_uri(url):
    """画像をサーバー側で取得して data URI で返す。
    ★スワイプUIは Streamlit のサンドボックス iframe 内で動くため、Drive等の外部URLを
      <img src> に直接入れるとクロスオリジンで表示されないことがある。ここで実体を取り込み、
      HTMLに data URI として埋め込む（＝確実に表示される）。失敗時はサムネイルURLにフォールバック。"""
    turl = _thumb_url(url)
    if not turl:
        return ""
    try:
        r = requests.get(turl, timeout=15)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            b64 = base64.b64encode(r.content).decode()
            return f"data:{r.headers['content-type']};base64,{b64}"
    except Exception:  # noqa: BLE001
        pass
    return turl  # 取得失敗時は素のサムネイルURL（表示されない可能性はあるが空よりまし）


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
                           "img": _img_data_uri(g("image_url"))})
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
