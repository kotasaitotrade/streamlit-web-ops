"""スマホ対応の価格管理ページ。
販売中商品の一覧を表示し、変更金額を入力してAmazonに反映できる。"""

import streamlit as st
import pandas as pd

from lib.sheets import materialize_secrets
from lib.auth import require_login, logout_button

st.set_page_config(page_title="価格管理", layout="wide", page_icon="💴")
materialize_secrets()

user = require_login()
logout_button()

import lib.amazon_api as amazon

# ============================================================
# 定数
# ============================================================
SUMMARY_SS_ID    = "1TEp7CTkDtApX8agWufw7v9w9hYkif58MLJcKwjz4mic"
MGMT_SS_ID       = "1Xb66vv997dWX9CIofuPNY23tuIQwoNFmm-hNBLbnBYo"  # satoアカウント
SUMMARY_SS_URL   = f"https://docs.google.com/spreadsheets/d/{SUMMARY_SS_ID}/edit"

# 商品一覧シートの列インデックス（amazon_api._SCOL_* と同じ値）
_COL_SKU    = 0   # A
_COL_STATUS = 1   # B
_COL_NAME   = 2   # C
_COL_COND   = 5   # F
_COL_PRICE  = 6   # G: 販売価格
_COL_CART   = 7   # H: カート獲得
_COL_CPRICE = 8   # I: カート価格
_COL_CCOND  = 9   # J: カート状態
_COL_FMIN   = 14  # O: 同コンFBA最低金額
_COL_CHANGE = 15  # P: 変更金額
_COL_ASIN   = 16  # Q: ASIN

# ============================================================
# スタイル
# ============================================================
st.markdown("""
<style>
    /* モバイル向け余白調整 */
    .block-container { padding: 1rem 0.75rem; }
    /* カート獲得バッジ */
    .badge-o  { color: #1e7e34; font-weight: bold; font-size: 1.1em; }
    .badge-x  { color: #999;    font-size: 1.0em; }
    .badge-d  { color: #856404; font-weight: bold; font-size: 1.1em; }
</style>
""", unsafe_allow_html=True)

st.title("💴 価格管理")

# ============================================================
# データ読み込み
# ============================================================
@st.cache_data(ttl=120)
def load_summary():
    svc = amazon._sheets_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SUMMARY_SS_ID,
        range="商品一覧!A1:Q4000",
    ).execute()
    return result.get("values", [])

def _cell(row, idx, default=""):
    if len(row) <= idx:
        return default
    val = row[idx]
    if val is None:
        return default
    return str(val).strip()

if st.button("🔄 最新データを読み込む", key="reload"):
    load_summary.clear()
    st.rerun()

with st.spinner("サマリーシート読み込み中..."):
    raw = load_summary()

if len(raw) < 2:
    st.warning("データがありません。まず「Amazon出品管理 → 商品サマリー → サマリーを更新」を実行してください。")
    st.stop()

# 販売中のみ抽出
items = []
for sheet_row_idx, row in enumerate(raw[1:], start=2):
    if _cell(row, _COL_STATUS) != "販売中":
        continue
    sku      = _cell(row, _COL_SKU)
    asin     = _cell(row, _COL_ASIN)
    page_url = f"https://www.amazon.co.jp/dp/{asin}" if asin else ""
    items.append({
        "sheet_row":    sheet_row_idx,
        "sku":          sku,
        "管理ID":       amazon._kanri_id_from_sku(sku) if sku else "",
        "商品名":       _cell(row, _COL_NAME),
        "URL":          page_url,
        "コンディション": _cell(row, _COL_COND),
        "販売価格":     _cell(row, _COL_PRICE),
        "カート獲得":   _cell(row, _COL_CART),
        "カート価格":   _cell(row, _COL_CPRICE),
        "カート状態":   _cell(row, _COL_CCOND),
        "同コンFBA最低金額": _cell(row, _COL_FMIN),
        "変更金額":     _cell(row, _COL_CHANGE),
    })

if not items:
    st.info("販売中の商品がありません。")
    st.stop()

st.caption(f"販売中: {len(items)} 件 | [スプレッドシートで開く]({SUMMARY_SS_URL})")
st.divider()

# ============================================================
# data_editor で表示・編集
# ============================================================
df = pd.DataFrame(items)

# 数値列を変換（NaN=空セル表示。Int64だと"None"テキストになるのでfloatを使う）
for col in ["販売価格", "カート価格", "同コンFBA最低金額", "変更金額"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

edited = st.data_editor(
    df[[
        "管理ID", "URL", "商品名", "コンディション",
        "販売価格", "カート獲得", "カート価格", "カート状態",
        "同コンFBA最低金額", "変更金額",
    ]],
    column_config={
        "管理ID":           st.column_config.TextColumn("管理ID",           disabled=True, width="small"),
        "URL":              st.column_config.LinkColumn("商品ページ",        disabled=True, display_text="開く", width="small"),
        "商品名":           st.column_config.TextColumn("商品名",           disabled=True, width="medium"),
        "コンディション":   st.column_config.TextColumn("状態",             disabled=True, width="small"),
        "販売価格":         st.column_config.NumberColumn("販売価格",        disabled=True, format="¥%d"),
        "カート獲得":       st.column_config.TextColumn("カート",           disabled=True, width="small"),
        "カート価格":       st.column_config.NumberColumn("カート価格",      disabled=True, format="¥%d"),
        "カート状態":       st.column_config.TextColumn("カート状態",        disabled=True, width="small"),
        "同コンFBA最低金額": st.column_config.NumberColumn("同コンFBA最低", disabled=True, format="¥%d"),
        "変更金額":         st.column_config.NumberColumn("変更金額 ✏️",     format="¥%d",  min_value=1),
    },
    hide_index=True,
    use_container_width=True,
    key="price_editor",
)

st.divider()

# ============================================================
# 変更金額をAmazonに反映
# ============================================================
c1, c2 = st.columns([1, 3])
dry_run = c1.checkbox("ドライラン", value=True, key="dry_price")
apply_btn = c2.button(
    "▶ ドライラン確認" if dry_run else "▶ Amazonに反映",
    type="secondary" if dry_run else "primary",
    key="apply_price",
)
if not dry_run:
    st.warning("⚠️ 本番モード: Amazonの出品価格を実際に変更します。")

if apply_btn:
    # editedのindex と df のindexを照合して変更金額を取得
    changes = []
    for i, row_edit in edited.iterrows():
        new_val = row_edit["変更金額"]
        if pd.isna(new_val) or new_val == 0:
            continue
        orig = items[i]
        changes.append({
            "sku":         orig["sku"],
            "kanri_id":    orig["管理ID"],
            "sheet_row":   orig["sheet_row"],
            "current":     int(float(orig["販売価格"])) if orig["販売価格"] not in ("", None) else None,
            "new_price":   int(new_val),
        })

    if not changes:
        st.warning("変更金額が入力されている行がありません。")
    else:
        # 変更金額をサマリーシートP列に書き込んでから run_set_price_from_summary を呼ぶ
        st.write(f"対象: {len(changes)} 件")
        log_area = st.empty()
        lines = []

        def _log(msg):
            lines.append(msg)
            log_area.markdown(
                '<div style="background:#1e1e1e;color:#d4d4d4;font-family:monospace;'
                'font-size:12px;padding:10px;border-radius:6px;white-space:pre-wrap;'
                f'max-height:300px;overflow-y:auto">' + "\n".join(lines[-60:]) + "</div>",
                unsafe_allow_html=True,
            )

        if not dry_run:
            # P列に変更金額を書き込む
            svc = amazon._sheets_service()
            for ch in changes:
                svc.spreadsheets().values().update(
                    spreadsheetId=SUMMARY_SS_ID,
                    range=f"商品一覧!P{ch['sheet_row']}",
                    valueInputOption="RAW",
                    body={"values": [[str(ch["new_price"])]]},
                ).execute()
            _log(f"P列（変更金額）を {len(changes)} 件書き込みました")

        # Amazon反映
        for ch in changes:
            cur_label = f"{ch['current']:,}円" if ch["current"] else "不明"
            _log(f"[{ch['kanri_id']}] {cur_label} → {ch['new_price']:,}円")
            if dry_run:
                _log(f"  → [ドライラン] スキップ")
                continue
            try:
                from sp_api.api import ListingsItems
                from sp_api.base import Marketplaces as MK
                li = ListingsItems(credentials=amazon._sp_creds(), marketplace=MK.JP)
                li.patch_listings_item(
                    sellerId=amazon._seller_id(), sku=ch["sku"],
                    marketplaceIds=[amazon._marketplace_id()],
                    body={
                        "productType": "PRODUCT",
                        "patches": [{"op": "replace", "path": "/attributes/purchasable_offer",
                                     "value": [{"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(ch["new_price"])}]}]}]}],
                    },
                )
                # サマリーG列（販売価格）更新
                svc.spreadsheets().values().update(
                    spreadsheetId=SUMMARY_SS_ID,
                    range=f"商品一覧!G{ch['sheet_row']}",
                    valueInputOption="RAW",
                    body={"values": [[str(ch["new_price"])]]},
                ).execute()
                _log(f"  → 更新完了")
                import time; time.sleep(1)
            except Exception as e:
                _log(f"  → エラー: {e}")

        if dry_run:
            st.info("ドライラン完了。チェックを外して「Amazonに反映」を押すと実際に変更されます。")
        else:
            st.success("✅ 完了しました")
            load_summary.clear()
