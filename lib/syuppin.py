"""撮影・出品入力ページのロジック。

外注さん（作業者）が各自の「商品管理」スプレッドシートへ、撮影・出品データを
Web から入力する。認証は lib.auth.require_login（Google OAuth + users シート）を
使い、Sheets アクセスは lib.sheets.get_client()（kotasaito の OAuth トークン）で行う。

■ 重要: onEdit(GAS) は API 書き込みでは発火しない
   各シートの「ステータス→2.写真撮影済み で撮影日を自動入力」は Apps Script の
   onEdit トリガーで画面 UI 編集時のみ動く。API 経由では撮影日が入らないため、
   ステータスを「2.写真撮影済み」にした行は撮影日が空なら当日(JST)を書き込む。
   撮影日は system_trade/kyuryo_calc.py の給料計算の基準列。

■ 安全書き込み
   シート全体クリアはせず、変更されたセルだけを行番号で照合し gspread の
   batch_update で部分更新する（仕入れ自動化との並行書き込み衝突を回避）。
"""
from __future__ import annotations

import datetime

import pandas as pd

from .sheets import get_client

SHEET_NAME = "商品管理"

# 作業者キー → 商品管理スプレッドシートID（system_trade/config/shiire_data_input.json と一致）
SPREADSHEET_IDS = {
    "kota": "1PNN-tEm0w2byvVRfQ3UUNlPFOWj79qWDy6_pefDx1tA",
    "kudo": "1keLLdpDRu2l9AjHyM6qRe_W8FFH_Jtl-isb1XFp8MzA",
    "kaho": "1pN1omlQj-V7n9pZi18IHFdjSgIvVO-Wg4WVdGTnb37I",
    "CHIZURU": "1a3GMaG_v-w1fEU1S6wFWAzp1vbTIRkry0OXegeHzvB8",
    "sato": "1Xb66vv997dWX9CIofuPNY23tuIQwoNFmm-hNBLbnBYo",
    "kaori": "1AkyYaNE_oqr-lMNCQZjTASQY9xSnIQcpzeytfNcK4uk",
}
# 表示名 → 作業者キー（users シートに worker_key 未設定でも表示名で推定するため）
NAME_TO_KEY = {
    "工藤大樹": "kudo", "齋藤航太": "kota", "西千鶴": "CHIZURU",
    "矢野加帆": "kaho", "佐藤サエ": "sato", "星原香織": "kaori",
}
KEY_LABEL = {v: f"{n}（{v}）" for n, v in NAME_TO_KEY.items()}

# 撮影・出品で作業者が入力する列（作業の流れ順・実在する列だけ動的採用）
EDITABLE_COLS = [
    "型番など", "状態", "状態-撮影", "販売価格", "ステータス", "計算用",
    "出品サイト", "SKU", "ASIN", "状態-仕入れ",
    "商品情報入力-非常に良い", "商品情報入力-良い", "状態2",
    "画像", "不良", "備考", "仕入れ特記事項",
]
ID_COLS = ["管理ID"]
READONLY_COLS = ["仕入れ値", "仕入れ日", "仕入れ元", "管理者", "撮影日"]

COL_HELP = {
    "型番など": "商品の型番・色・容量など（例: Switch有機EL ホワイト）",
    "状態": "メルカリ等の商品状態を選択",
    "状態-撮影": "Amazon出品時のコンディション",
    "販売価格": "出品する販売価格（半角数字）",
    "ステータス": "撮影が終わったら『2.写真撮影済み』を選ぶ → 撮影日は自動で入ります",
    "計算用": "ばら売り部品のときだけ選択（給料計算用）",
    "出品サイト": "出品先（amazon / メルカリ 等）",
    "SKU": "Amazon出品用SKU",
    "ASIN": "AmazonのASIN",
    "商品情報入力-非常に良い": "コンディション『非常に良い』の説明文",
    "商品情報入力-良い": "コンディション『良い』の説明文",
    "画像": "商品画像のURL。撮影写真はドライブ等にアップし共有URLを貼付（仕入れ元URLが自動で入っていればそのままでOK）",
    "備考": "自由メモ",
    "不良": "不良がある場合に記入",
}

STATUS_OPTIONS = [
    "", "1.1.発注済み", "1.2.受け取り済み", "1.3.動作確認済み", "1.4.一部返金処理",
    "2.写真撮影済み", "3.発送待ち", "3.出品済み", "キャンセル",
]
CONDITION_OPTIONS = ["", "新品、未使用", "未使用に近い", "目立った傷や汚れなし", "やや傷や汚れあり", "傷や汚れあり", "全体的に状態が悪い"]
AMAZON_CONDITION_OPTIONS = ["", "新品", "ほぼ新品", "非常に良い", "良い", "可"]
KEISAN_OPTIONS = ["", "ばら売り", "100", "200"]
SITE_OPTIONS = ["", "amazon", "メルカリ", "ヤフフリ", "ヤフオク", "楽天ラクマ"]
SELECT_COLS = {"ステータス", "状態", "状態-撮影", "計算用", "出品サイト"}

PRE_SHOOT_STATUSES = {"", "1.1.発注済み", "1.2.受け取り済み", "1.3.動作確認済み", "1.4.一部返金処理"}
DONE_SHOOT_STATUS = "2.写真撮影済み"


# ============================================================
# 作業者キー解決
# ============================================================
def is_manager(user: dict) -> bool:
    return str(user.get("role", "")).strip().lower() in {"admin", "manager"}


def resolve_worker_key(user: dict):
    """作業者ユーザーの担当キーを返す。users シートの worker_key 列 →
    表示名から推定 の順。見つからなければ None。"""
    wk = str(user.get("worker_key", "")).strip()
    if wk in SPREADSHEET_IDS:
        return wk
    name = str(user.get("display_name", "")).strip()
    if name in NAME_TO_KEY:
        return NAME_TO_KEY[name]
    return None


# ============================================================
# Sheets I/O（gspread）
# ============================================================
def _worksheet(spreadsheet_id):
    client = get_client()
    if client is None:
        raise RuntimeError("Google Sheets 認証に失敗しました（secrets の gspread_token を確認）")
    return client.open_by_key(spreadsheet_id).worksheet(SHEET_NAME)


def col_to_a1(col_idx: int) -> str:
    s, n = "", col_idx + 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def read_sheet(spreadsheet_id):
    """(headers, rows) を返す。"""
    values = _worksheet(spreadsheet_id).get_all_values()
    if not values:
        return [], []
    return values[0], values[1:]


def build_dataframe(headers, rows):
    """行番号付き DataFrame。__row はシート実行番号(ヘッダー=1 なので先頭データ行=2)。"""
    norm = [r + [""] * (len(headers) - len(r)) for r in rows]
    df = pd.DataFrame(norm, columns=headers) if norm else pd.DataFrame(columns=headers)
    df = df.astype(str)
    df.insert(0, "__row", [i + 2 for i in range(len(df))])
    return df


def today_jst() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y/%m/%d")


def diff_updates(headers, orig_df, edited_df, row_numbers, shoot_dates):
    """行位置(iloc)で突き合わせ、変更セルの (row, col_idx, value) を返す。
    ステータスを 2.写真撮影済み にした行は撮影日が空なら当日を補完（既存値は上書きしない）。"""
    updates, changed_rows = [], 0
    col_index = {h: i for i, h in enumerate(headers)}
    has_shoot = "撮影日" in col_index
    edit_cols = list(edited_df.columns)

    def norm(v):
        if v is None:
            return ""
        if isinstance(v, float) and pd.isna(v):
            return ""
        s = str(v).strip()
        return "" if s.lower() == "none" else s

    for i in range(len(edited_df)):
        rownum = int(row_numbers[i])
        erow, orow = edited_df.iloc[i], orig_df.iloc[i]
        touched = False
        for h in edit_cols:
            if h == "" or h not in col_index:
                continue
            nv, ov = norm(erow.get(h)), norm(orow.get(h))
            if nv != ov:
                updates.append((rownum, col_index[h], nv))
                touched = True
        if has_shoot and "ステータス" in edit_cols:
            ns, os_ = norm(erow.get("ステータス")), norm(orow.get("ステータス"))
            cur = norm(shoot_dates[i]) if i < len(shoot_dates) else ""
            if ns == DONE_SHOOT_STATUS and os_ != DONE_SHOOT_STATUS and not cur:
                updates.append((rownum, col_index["撮影日"], today_jst()))
                touched = True
        if touched:
            changed_rows += 1
    return updates, changed_rows


def apply_updates(spreadsheet_id, updates):
    """updates を gspread batch_update で部分反映。書き込んだセル数を返す。"""
    if not updates:
        return 0
    data = [{"range": f"{col_to_a1(c)}{r}", "values": [[v]]} for (r, c, v) in updates]
    _worksheet(spreadsheet_id).batch_update(data, value_input_option="USER_ENTERED")
    return len(data)


def append_row(spreadsheet_id, headers, record):
    """record(dict) を 1 行追記。"""
    row = [record.get(h, "") for h in headers]
    _worksheet(spreadsheet_id).append_row(row, value_input_option="USER_ENTERED")
