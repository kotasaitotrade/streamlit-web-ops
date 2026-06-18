"""スマホ対応の価格管理ページ（カード形式）。
販売中・納品中商品をカード表示し、変更金額を入力してAmazonに反映する。"""

import streamlit as st

from lib.sheets import materialize_secrets
from lib.auth import require_login, logout_button

materialize_secrets()

user = require_login()
logout_button()

import lib.amazon_api as amazon

# ============================================================
# 定数
# ============================================================
SUMMARY_SS_ID  = "1TEp7CTkDtApX8agWufw7v9w9hYkif58MLJcKwjz4mic"
SUMMARY_SS_URL = f"https://docs.google.com/spreadsheets/d/{SUMMARY_SS_ID}/edit"

_COL_SKU    = 0
_COL_STATUS = 1
_COL_NAME   = 2
_COL_COND   = 5
_COL_PRICE  = 6
_COL_CART   = 7
_COL_CPRICE = 8
_COL_CCOND  = 9
_COL_FMIN   = 14
_COL_CHANGE = 15
_COL_ASIN   = 16

# ============================================================
# スタイル（スマホ向け）
# ============================================================
st.markdown("""
<style>
    .block-container { padding: 0.75rem 0.75rem 2rem; }
    /* カード風のexpander */
    details[data-testid="stExpander"] {
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        margin-bottom: 6px;
        background: #fff;
    }
    summary[data-testid="stExpanderToggleIcon"] { font-size: 15px; }
    /* 入力欄の幅を広げる */
    input[type="number"] { font-size: 16px !important; }
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

def _fmt_price(val: str) -> str:
    try:
        return f"¥{int(float(val)):,}"
    except Exception:
        return "-"

# ============================================================
# 更新ボタン
# ============================================================
reload_clicked = st.button("🔄 Amazonから最新データを取得", key="reload")
st.caption("⏱ 商品数に応じて数分かかります。完了までこのページを開いたままにしてください。")
if reload_clicked:
    log_area = st.empty()
    lines = []
    try:
        for msg in amazon.run_create_summary_sheet(SUMMARY_SS_ID):
            lines.append(msg)
            log_area.markdown(
                '<div style="background:#1e1e1e;color:#d4d4d4;font-family:monospace;'
                'font-size:11px;padding:8px;border-radius:6px;white-space:pre-wrap;'
                f'max-height:180px;overflow-y:auto">' + "\n".join(lines[-30:]) + "</div>",
                unsafe_allow_html=True,
            )
        log_area.success("✅ 取得完了")
    except Exception as e:
        log_area.error(f"エラー: {e}")
    load_summary.clear()
    st.rerun()

try:
    with st.spinner("読み込み中..."):
        raw = load_summary()
except Exception as e:
    st.error(f"❌ データの読み込みに失敗しました（通信状況をご確認ください）: {e}")
    st.button("🔁 再読み込み", on_click=load_summary.clear)
    st.stop()

if len(raw) < 2:
    st.warning("データがありません。「Amazonから最新データを取得」を押してください。")
    st.stop()

# 販売中・納品中のみ抽出
items = []
for sheet_row_idx, row in enumerate(raw[1:], start=2):
    status = _cell(row, _COL_STATUS)
    if status not in ("販売中", "納品中"):
        continue
    sku  = _cell(row, _COL_SKU)
    asin = _cell(row, _COL_ASIN)
    items.append({
        "sheet_row": sheet_row_idx,
        "sku":       sku,
        "管理ID":    amazon._kanri_id_from_sku(sku) if sku else "",
        "商品名":    _cell(row, _COL_NAME),
        "URL":       f"https://www.amazon.co.jp/dp/{asin}" if asin else "",
        "状態":      status,
        "コンディション": _cell(row, _COL_COND),
        "販売価格":  _cell(row, _COL_PRICE),
        "カート獲得": _cell(row, _COL_CART),
        "カート価格": _cell(row, _COL_CPRICE),
        "カート状態": _cell(row, _COL_CCOND),
        "同コンFBA最低": _cell(row, _COL_FMIN),
        "変更金額_default": _cell(row, _COL_CHANGE),
    })

if not items:
    st.info("販売中・納品中の商品がありません。")
    st.stop()

# カート獲得状況フィルタ（値下げ対象＝カート未獲得を素早く絞り込む）
all_items = list(items)   # フィルタ前の全件（反映時はこちらを走査）
total_n = len(items)
cart_filter = st.radio(
    "カート獲得で絞り込み",
    ["すべて", "✗ 未獲得のみ", "○ 獲得のみ"],
    horizontal=True,
    label_visibility="collapsed",
    key="cart_filter",
)
if cart_filter == "✗ 未獲得のみ":
    items = [it for it in items if it["カート獲得"] != "○"]
elif cart_filter == "○ 獲得のみ":
    items = [it for it in items if it["カート獲得"] == "○"]

st.caption(f"表示 {len(items)} 件 / 販売中・納品中 {total_n} 件 | [スプレッドシート]({SUMMARY_SS_URL})")

if not items:
    st.info("条件に一致する商品がありません。フィルタを変更してください。")
    st.stop()

# 入力した変更金額を行ごとに保持する辞書（フィルタで非表示になっても値が消えないように）
if "price_inputs" not in st.session_state:
    st.session_state["price_inputs"] = {}
price_inputs = st.session_state["price_inputs"]

# ============================================================
# カード一覧
# ============================================================
for i, item in enumerate(items):
    cart = item["カート獲得"]
    cart_icon = "🛒 " if cart == "○" else ("△ " if cart == "△" else "")
    status_badge = "🟡 " if item["状態"] == "納品中" else ""
    price_str = _fmt_price(item["販売価格"])
    cond_str  = item["コンディション"] or item["状態"]

    header = f"{status_badge}{cart_icon}{item['商品名'][:22]}  　{cond_str}  　{price_str}"

    with st.expander(header, expanded=False):
        # フル商品名（ヘッダーは22文字で切れるため、展開時に全文を表示）
        st.markdown(f"**{item['商品名']}**")
        # 詳細情報
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(f"**管理ID** : {item['管理ID']}")
            st.markdown(f"**状態** : {item['状態']}")
            st.markdown(f"**カート** : {cart or '－'}")
            st.markdown(f"**カート価格** : {_fmt_price(item['カート価格'])}")
        with col_r:
            st.markdown(f"**カート状態** : {item['カート状態'] or '－'}")
            fmin = item["同コンFBA最低"]
            st.markdown(f"**同コンFBA最低** : {_fmt_price(fmin) if fmin else '－'}")
            if item["URL"]:
                st.link_button("🔗 商品ページを開く", item["URL"], use_container_width=True)

        # 変更金額入力。既入力(price_inputs)を優先し、無ければサマリーP列の値を初期表示
        row_key = item["sheet_row"]
        default_val = price_inputs.get(row_key)
        if default_val is None:
            try:
                default_val = int(float(item["変更金額_default"])) if item["変更金額_default"] else None
            except Exception:
                default_val = None

        entered = st.number_input(
            "💰 変更金額（円）",
            min_value=1,
            value=default_val,
            step=100,
            placeholder="変更しない場合は空欄のまま",
            key=f"price_{row_key}",  # 行固有キー（フィルタで位置が変わってもズレない）
            label_visibility="visible",
        )
        # 入力値を辞書へ同期（フィルタで非表示になっても保持される）
        if entered:
            price_inputs[row_key] = int(entered)
        else:
            price_inputs.pop(row_key, None)

st.divider()

# ============================================================
# 反映ボタン
# ============================================================
c1, c2 = st.columns([1, 2])
dry_run = c1.checkbox("ドライラン", value=True, key="dry_price")
apply_btn = c2.button(
    "▶ ドライラン確認" if dry_run else "▶ Amazonに反映",
    type="secondary" if dry_run else "primary",
    key="apply_price",
    use_container_width=True,
)
if not dry_run:
    st.warning("⚠️ 本番モード: Amazonの出品価格を実際に変更します。")

# 入力された変更内容を毎回計算（price_inputs辞書から全件走査。フィルタ非表示分も拾う）
changes = []
for item in all_items:
    new_val = price_inputs.get(item["sheet_row"])
    if not new_val:
        continue
    changes.append({
        "sku":       item["sku"],
        "kanri_id":  item["管理ID"],
        "name":      item["商品名"],
        "sheet_row": item["sheet_row"],
        "current":   int(float(item["販売価格"])) if item["販売価格"] else None,
        "new_price": int(new_val),
    })

# 実行トリガー判定（ドライランは即実行、本番は確認ステップを挟む）
run_now = False
if apply_btn:
    if not changes:
        st.warning("変更金額が入力されている商品がありません。")
    elif dry_run:
        run_now = True
    else:
        st.session_state["price_confirm"] = True

# 本番反映の確認ステップ
if st.session_state.get("price_confirm") and not dry_run and changes:
    st.warning(f"⚠️ {len(changes)} 件の出品価格を実際に変更します。内容を確認してください。")
    for ch in changes:
        cur = f"{ch['current']:,}円" if ch["current"] else "不明"
        st.markdown(f"- **{ch['name'][:30]}**　{cur} → **{ch['new_price']:,}円**")
    cc1, cc2 = st.columns([1, 2])
    if cc1.button("✅ はい、反映する", type="primary", key="price_confirm_yes"):
        st.session_state["price_confirm"] = False
        run_now = True
    if cc2.button("キャンセル", key="price_confirm_no"):
        st.session_state["price_confirm"] = False
        st.rerun()

if run_now:
    if True:
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
            svc = amazon._sheets_service()
            for ch in changes:
                svc.spreadsheets().values().update(
                    spreadsheetId=SUMMARY_SS_ID,
                    range=f"商品一覧!P{ch['sheet_row']}",
                    valueInputOption="RAW",
                    body={"values": [[str(ch["new_price"])]]},
                ).execute()
            _log(f"P列（変更金額）を {len(changes)} 件書き込みました")

        from sp_api.api import ListingsItems
        from sp_api.base import Marketplaces as MK
        li = None if dry_run else ListingsItems(credentials=amazon._sp_creds(), marketplace=MK.JP)

        import time
        for ch in changes:
            cur = f"{ch['current']:,}円" if ch["current"] else "不明"
            _log(f"[{ch['kanri_id']}] {cur} → {ch['new_price']:,}円")
            if dry_run:
                _log("  → [ドライラン] スキップ")
                continue
            try:
                li.patch_listings_item(
                    sellerId=amazon._seller_id(), sku=ch["sku"],
                    marketplaceIds=[amazon._marketplace_id()],
                    body={
                        "productType": "PRODUCT",
                        "patches": [{"op": "replace", "path": "/attributes/purchasable_offer",
                                     "value": [{"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(ch["new_price"])}]}]}]}],
                    },
                )
                svc.spreadsheets().values().update(
                    spreadsheetId=SUMMARY_SS_ID,
                    range=f"商品一覧!G{ch['sheet_row']}",
                    valueInputOption="RAW",
                    body={"values": [[str(ch["new_price"])]]},
                ).execute()
                _log("  → 更新完了")
                time.sleep(1)
            except Exception as e:
                _log(f"  → エラー: {e}")

        if dry_run:
            st.info("ドライラン完了。チェックを外して「Amazonに反映」を押すと実際に変更されます。")
        else:
            st.success("✅ 完了しました")
            load_summary.clear()
