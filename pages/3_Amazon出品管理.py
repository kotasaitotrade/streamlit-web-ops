"""Amazon せどりツール ページ。
ASIN取得 / 自動出品 / 価格調整 / FNSKUラベル生成 を Streamlit Cloud 上で実行する。"""

import streamlit as st
from datetime import datetime

from lib.sheets import materialize_secrets
from lib.auth import require_login, logout_button

st.set_page_config(page_title="Amazon出品管理", layout="wide", page_icon="📦")
materialize_secrets()

user = require_login()
logout_button()

import lib.amazon_api as amazon  # materialize_secrets() 後に import

# ============================================================
# スタイル
# ============================================================
st.markdown("""
<style>
    .stTabs [data-baseweb="tab"] { padding: 8px 20px; font-size: 15px; }
    .log-box {
        background: #1e1e1e; color: #d4d4d4;
        font-family: monospace; font-size: 12px;
        padding: 12px; border-radius: 6px;
        white-space: pre-wrap; max-height: 420px; overflow-y: auto;
    }
    .stat-card {
        background: #f0f4ff; border-left: 4px solid #1d3a8a;
        padding: 10px 14px; border-radius: 4px; margin: 4px 0;
    }
</style>
""", unsafe_allow_html=True)

st.title("📦 Amazon出品管理")
st.caption("ASIN自動取得 / 自動出品 / 価格調整 / FNSKUラベル生成 / FBA納品プラン作成")

# ============================================================
# アカウント選択
# ============================================================
ACCOUNTS = {
    "sato": "1Xb66vv997dWX9CIofuPNY23tuIQwoNFmm-hNBLbnBYo",
    "kudo": "1keLLdpDRu2l9AjHyM6qRe_W8FFH_Jtl-isb1XFp8MzA",
}

account = st.selectbox("アカウント", list(ACCOUNTS.keys()), key="account_select")
ss_id = ACCOUNTS[account]

st.divider()


# ============================================================
# サイドバー: スプレッドシート概要
# ============================================================
@st.cache_data(ttl=60)
def _get_status_counts(spreadsheet_id: str):
    try:
        return amazon.get_status_counts(spreadsheet_id), None
    except Exception as e:
        return {}, str(e)


def _show_sidebar(spreadsheet_id: str):
    counts, err = _get_status_counts(spreadsheet_id)
    if err:
        st.sidebar.warning(f"スプシ読み込みエラー: {err[:60]}")
        return
    st.sidebar.markdown(f"### 📊 {account} スプレッドシート状況")
    order = [
        ("3.出品済み",       "🟢", "出品済み"),
        ("3.納品済み",       "🟡", "納品済み（出品待ち）"),
        ("1.3.動作確認済み", "🔵", "動作確認済み"),
        ("1.1.発注済み",     "⚪", "発注済み"),
        ("キャンセル",       "🔴", "キャンセル"),
    ]
    for key, icon, label in order:
        n = counts.get(key, 0)
        if n:
            st.sidebar.markdown(
                f'<div class="stat-card">{icon} <b>{n}件</b>　{label}</div>',
                unsafe_allow_html=True,
            )
    if st.sidebar.button("🔄 更新", key="sidebar_refresh"):
        _get_status_counts.clear()
        st.rerun()


_show_sidebar(ss_id)


# ============================================================
# ヘルパー: generator → ログ表示
# ============================================================
def _stream_logs(gen, placeholder):
    lines = []
    for msg in gen:
        lines.append(msg)
        placeholder.markdown(
            '<div class="log-box">' + "\n".join(lines[-120:]) + "</div>",
            unsafe_allow_html=True,
        )
    return lines


# ============================================================
# タブ
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🔍 ASIN取得",
    "📤 自動出品",
    "💴 価格調整",
    "🏷️ FNSKUラベル",
    "🚚 FBA納品",
])


# ── タブ1: ASIN取得 ──────────────────────────────────────────
with tab1:
    st.subheader("🔍 ASIN自動取得")
    st.markdown("""
**ASIN列が空** で **仕入れ特記事項に型番・商品名が入っている行** を対象に、
Amazon カタログを検索して ASIN を自動書き込みします。
""")
    run_asin = st.button("▶ 実行する", key="run_asin", type="primary")

    if run_asin:
        st.warning("⚠️ スプレッドシートの ASIN 列を上書きします。よろしいですか？")
        c_ok, c_cancel = st.columns([1, 4])
        confirmed_asin = c_ok.button("✅ 実行する", key="run_asin_confirm", type="primary")
        c_cancel.button("キャンセル", key="run_asin_cancel")
        if confirmed_asin:
            log_area = st.empty()
            with st.spinner("ASIN 検索中... (1件あたり約1秒)"):
                lines = _stream_logs(amazon.run_asin_lookup(dry_run=False, spreadsheet_id=ss_id), log_area)
            st.success("✅ 完了しました")
            _get_status_counts.clear()


# ── タブ2: 自動出品 ──────────────────────────────────────────
with tab2:
    st.subheader("📤 自動出品")
    st.markdown("""
ステータスが **`3.納品済み`** の行を対象に、Amazon FBA へ出品登録します。
成功するとステータスが **`3.出品済み`** に更新されます。
""")

    c1, c2 = st.columns([1, 4])
    with c1:
        dry2 = st.checkbox("ドライラン", value=True, key="dry2")
    run_listing = c2.button(
        "▶ ドライラン実行" if dry2 else "▶ 本番実行",
        key="run_listing",
        type="secondary" if dry2 else "primary",
    )
    if not dry2:
        st.warning("⚠️ 本番モード: SP-API に実際に出品リクエストを送信します。")

    if run_listing:
        log_area = st.empty()
        label = "ドライラン" if dry2 else "本番"
        with st.spinner(f"出品処理中 [{label}]..."):
            lines = _stream_logs(amazon.run_auto_listing(dry_run=dry2, spreadsheet_id=ss_id), log_area)
        st.success("✅ 完了しました")
        if not dry2:
            _get_status_counts.clear()


# ── タブ3: 価格調整 ──────────────────────────────────────────
with tab3:
    st.subheader("💴 価格自動調整")
    st.markdown("""
ステータスが **`3.出品済み`** の全商品の BuyBox 価格を取得し、自動調整します。

| 状況 | 対応 |
|---|---|
| BuyBox より高い | BuyBox - 1円（アンダーカット） |
| BuyBox より 20% 以上安い | 現在価格を引き上げ（上限: 元値） |
| 上記以外 | 変更なし |

下限: **仕入れ値 × 1.15**（15% マージン確保）
""")

    c1, c2 = st.columns([1, 4])
    with c1:
        dry3 = st.checkbox("ドライラン", value=True, key="dry3")
    run_reprice = c2.button(
        "▶ ドライラン実行" if dry3 else "▶ 本番実行",
        key="run_reprice",
        type="secondary" if dry3 else "primary",
    )
    if not dry3:
        st.warning("⚠️ 本番モード: SP-API に実際に価格変更リクエストを送信します。")

    if run_reprice:
        log_area = st.empty()
        label = "ドライラン" if dry3 else "本番"
        with st.spinner(f"価格調整中 [{label}]... (1件あたり約2秒)"):
            lines = _stream_logs(amazon.run_auto_reprice(dry_run=dry3, spreadsheet_id=ss_id), log_area)
        st.success("✅ 完了しました")
        if not dry3:
            _get_status_counts.clear()


# ── タブ4: FNSKUラベル ───────────────────────────────────────
with tab4:
    st.subheader("🏷️ FNSKUラベル生成")
    st.markdown("""
ステータスが **`3.出品済み`** の商品について、SP-API から FNSKU を取得し、
**バーコード + 商品情報 + Google Drive 画像** を組み合わせた A4 PDF を生成します。

**Google Drive の画像フォルダ構成:**
```
親フォルダ
  └── {SKU名}/   ← SKU 名のフォルダを作成して画像を入れる
        ├── 01.jpg
        └── ...（最大6枚使用）
```
""")

    c1, c2 = st.columns([2, 3])
    with c1:
        target_sku = st.text_input(
            "SKU 絞り込み（任意）",
            placeholder="例: RS00006ST14200260422  （空白=全件）",
            key="target_sku",
        )
    with c2:
        st.markdown("<br>", unsafe_allow_html=True)
        run_labels = st.button("▶ PDF 生成", key="run_labels", type="primary", use_container_width=True)

    if run_labels:
        sku_filter = target_sku.strip()
        with st.spinner("PDF 生成中... (SP-API と Drive にアクセスします)"):
            pdf_bytes, logs = amazon.run_fnsku_labels(target_sku=sku_filter, spreadsheet_id=ss_id)

        log_html = "\n".join(logs[-60:])
        st.markdown(f'<div class="log-box">{log_html}</div>', unsafe_allow_html=True)

        if pdf_bytes:
            st.success("✅ PDF を生成しました")
            filename = f"fnsku_labels_{datetime.now().strftime('%Y-%m-%d')}.pdf"
            if sku_filter:
                filename = f"fnsku_{sku_filter}_{datetime.now().strftime('%Y-%m-%d')}.pdf"
            st.download_button(
                label="⬇️ PDF をダウンロード",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf",
                use_container_width=True,
            )
            st.caption(f"📄 {filename}　{len(pdf_bytes) // 1024} KB")
        else:
            st.info("生成対象がありませんでした。ステータス '3.出品済み' の行を確認してください。")


# ── タブ5: FBA納品 ───────────────────────────────────────────
with tab5:
    st.subheader("🚚 FBA納品")
    st.markdown("""
ステータス **`2.写真撮影済み`** の商品を対象に、出品登録・FNSKUラベル PDF・FBA 納品プラン作成を自動実行します。

| ステップ | 方法 |
|---|---|
| ① 出品登録 + FNSKUラベル PDF + 納品プラン作成 | **このページで自動実行** |
| ② 輸送方法の選択・確定 | **このページで自動実行** |
| ③ 梱包・発送 | 自宅で手動 |
| ④ 受取確認 | このページで確認 |
""")

    st.divider()
    st.markdown("#### ① 出品登録 + FNSKUラベル PDF 生成 + 納品プラン作成")

    c1, c2 = st.columns([1, 4])
    with c1:
        dry5 = st.checkbox("ドライラン", value=True, key="dry5")
    run_fba = c2.button(
        "▶ ドライラン実行" if dry5 else "▶ 本番実行",
        key="run_fba",
        type="secondary" if dry5 else "primary",
        use_container_width=True,
    )
    if not dry5:
        st.warning("⚠️ 本番モード: SP-API に出品登録リクエストを送信します。")

    if run_fba:
        log_area5 = st.empty()
        label = "ドライラン" if dry5 else "本番"
        with st.spinner(f"処理中 [{label}]..."):
            logs5, pdf5, plan5 = amazon.run_fba_inbound(
                account_name=account, dry_run=dry5, spreadsheet_id=ss_id
            )

        log_html5 = "\n".join(logs5[-120:])
        log_area5.markdown(f'<div class="log-box">{log_html5}</div>', unsafe_allow_html=True)

        if pdf5:
            st.success("✅ FNSKUラベル PDF を生成しました")
            fname = f"fnsku_fba_{datetime.now().strftime('%Y-%m-%d')}.pdf"
            st.download_button(
                label="⬇️ FNSKUラベル PDF をダウンロード",
                data=pdf5,
                file_name=fname,
                mime="application/pdf",
                use_container_width=True,
            )

        if plan5 and not dry5:
            st.session_state["fba_plan_result"] = plan5
            st.session_state.pop("fba_transport_options", None)  # 前回の選択をリセット

        if not dry5:
            _get_status_counts.clear()

    st.divider()

    # ── ② 輸送方法の選択・確定 ────────────────────────────────
    st.markdown("#### ② 輸送方法の選択・確定（自動）")

    plan_result = st.session_state.get("fba_plan_result", {})
    plan_id_val = plan_result.get("plan_id", "")
    placement_id_val = plan_result.get("placement_option_id", "")
    shipment_ids_val = plan_result.get("shipment_ids", [])

    # プランID 手動入力（セッションリロード後も使えるよう）
    with st.expander("プラン情報（① 実行後に自動入力。手動入力も可）", expanded=not plan_id_val):
        plan_id_input = st.text_input("プランID", value=plan_id_val, key="fba_plan_id_input",
                                      placeholder="例: wf12345abc-...")
        placement_id_input = st.text_input("配置オプションID", value=placement_id_val,
                                           key="fba_placement_id_input")
        shipment_ids_input = st.text_input(
            "シップメントID（カンマ区切り）",
            value=",".join(shipment_ids_val),
            key="fba_shipment_ids_input",
        )

    _plan_id = plan_id_input.strip() or plan_id_val
    _placement_id = placement_id_input.strip() or placement_id_val
    _shipment_ids = [s.strip() for s in shipment_ids_input.split(",") if s.strip()] or shipment_ids_val

    st.markdown("**箱の情報を入力してください**")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        num_boxes = st.number_input("箱の個数", min_value=1, value=1, step=1, key="fba_num_boxes")
    with c2:
        box_length = st.number_input("長さ (cm)", min_value=1.0, value=30.0, step=1.0, key="fba_box_length")
    with c3:
        box_width = st.number_input("幅 (cm)", min_value=1.0, value=30.0, step=1.0, key="fba_box_width")
    with c4:
        box_height = st.number_input("高さ (cm)", min_value=1.0, value=30.0, step=1.0, key="fba_box_height")
    with c5:
        box_weight = st.number_input("重量 (kg/箱)", min_value=0.1, value=5.0, step=0.1, key="fba_box_weight")

    get_transport = st.button(
        "▶ 輸送オプションを取得",
        key="get_transport",
        type="primary",
        disabled=not _plan_id,
    )

    if get_transport:
        if not _plan_id or not _placement_id or not _shipment_ids:
            st.error("プランID・配置オプションID・シップメントIDをすべて入力してください（① を先に実行してください）。")
        else:
            boxes = [
                {"length_cm": box_length, "width_cm": box_width,
                 "height_cm": box_height, "weight_kg": box_weight}
                for _ in range(int(num_boxes))
            ]
            with st.spinner("輸送オプションを取得中..."):
                result = amazon.get_fba_transportation_options(
                    account_name=account,
                    plan_id=_plan_id,
                    placement_option_id=_placement_id,
                    shipment_ids=_shipment_ids,
                    boxes=boxes,
                )
            if result["error"]:
                st.error(f"エラー: {result['error']}")
            elif not result["options"]:
                st.warning("輸送オプションが見つかりませんでした。")
            else:
                st.session_state["fba_transport_options"] = result["options"]
                st.success(f"✅ {len(result['options'])} 件の輸送オプションを取得しました")

    # オプション選択 → 確定
    transport_options = st.session_state.get("fba_transport_options", [])
    if transport_options:
        st.markdown("**輸送方法を選択してください**")

        def _option_label(opt):
            carrier = opt.get("carrier", {}).get("name", "不明")
            mode = opt.get("shippingMode", "")
            cost = opt.get("quote", {}).get("cost", {})
            amount = cost.get("amount", 0)
            code = cost.get("code", "JPY")
            return f"{carrier}  {mode}  {code} {amount:,.0f}"

        option_labels = [_option_label(o) for o in transport_options]
        selected_label = st.radio("輸送オプション", option_labels, key="fba_transport_radio")
        selected_idx = option_labels.index(selected_label)
        selected_opt = transport_options[selected_idx]

        confirm_transport = st.button("▶ 輸送方法を確定する", key="confirm_transport", type="primary")
        if confirm_transport:
            selections = [
                {
                    "shipment_id": selected_opt.get("shipmentId", sid),
                    "transportation_option_id": selected_opt["transportationOptionId"],
                }
                for sid in (_shipment_ids or [selected_opt.get("shipmentId", "")])
            ]
            with st.spinner("輸送方法を確定中..."):
                conf = amazon.confirm_fba_transportation(
                    account_name=account,
                    plan_id=_plan_id,
                    selections=selections,
                )
            if conf["error"]:
                st.error(f"確定エラー: {conf['error']}")
            else:
                st.success("✅ 輸送方法を確定しました！梱包・発送に進んでください。")
                st.session_state.pop("fba_transport_options", None)

    st.divider()
    st.markdown("#### ③ 受取確認（発送後に押す）")
    st.caption("ステータス `3.発送待ち` の商品について Amazon の受取状況を確認し、受取済みなら `3.出品済み` に更新します。")

    run_receipt = st.button("🔍 受取確認を実行", key="run_receipt", type="secondary")

    if run_receipt:
        log_area_r = st.empty()
        with st.spinner("Amazon の受取状況を確認中..."):
            lines_r = _stream_logs(amazon.run_receipt_check(spreadsheet_id=ss_id), log_area_r)
        st.success("✅ 完了しました")
        _get_status_counts.clear()
