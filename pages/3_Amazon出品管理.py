"""Amazon せどりツール ページ。
ASIN取得 / 自動出品 / 価格調整 / FNSKUラベル生成 を Streamlit Cloud 上で実行する。"""

import streamlit as st
from datetime import datetime

from lib.sheets import materialize_secrets
from lib.auth import require_login, logout_button

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
st.caption("ASIN自動取得 / 価格調整 / FNSKUラベル生成 / FBA納品プラン作成")

# ============================================================
# アカウント選択
# ============================================================
ACCOUNTS = {
    "sato": "1Xb66vv997dWX9CIofuPNY23tuIQwoNFmm-hNBLbnBYo",
    "kudo": "1keLLdpDRu2l9AjHyM6qRe_W8FFH_Jtl-isb1XFp8MzA",
}
ACCOUNT_LABELS = {
    "sato": "sato（佐藤さん）",
    "kudo": "kudo（工藤さん）",
}

def _reset_confirm_flags():
    """アカウント切替時、本番実行の確認待ちを解除（別アカウントへの誤実行防止）。"""
    for _k in ("confirm_reprice", "confirm_set_min", "confirm_fba", "confirm_set_price",
               "confirm_bb", "asin_confirm_needed"):
        st.session_state.pop(_k, None)

account = st.selectbox(
    "アカウント",
    list(ACCOUNTS.keys()),
    format_func=lambda k: ACCOUNT_LABELS.get(k, k),
    key="account_select",
    on_change=_reset_confirm_flags,
)
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


# サマリーシート（商品一覧）。タブをまたいで使うのでここで定義。
SUMMARY_SS_ID = "1TEp7CTkDtApX8agWufw7v9w9hYkif58MLJcKwjz4mic"
SUMMARY_SS_URL = f"https://docs.google.com/spreadsheets/d/{SUMMARY_SS_ID}/edit"


# ============================================================
# タブ
# ============================================================
tab1, tab3, tab4, tab5, tab6 = st.tabs([
    "🔍 ASIN取得",
    "💴 価格調整",
    "🏷️ FNSKUラベル",
    "🚚 FBA納品",
    "📊 商品サマリー",
])


# ── タブ1: ASIN取得 ──────────────────────────────────────────
with tab1:
    st.subheader("🔍 ASIN自動取得")
    st.markdown("""
**ASIN列が空** で **仕入れ特記事項（L列）に型番・商品名が入っている行** を対象に、
Amazon カタログを検索して ASIN を自動書き込みします。

🔎 **誤書き込み防止**: 検索結果の商品名に型番が含まれる候補（✅型番一致）のみ書き込み、
確証がないものは **不採用** としてスキップします（⚠️で候補だけ表示）。
まず **ドライラン** で結果を確認してから本番実行してください。
""")
    c_asin1, c_asin2 = st.columns([1, 3])
    with c_asin1:
        dry_asin = st.checkbox("ドライラン", value=True, key="dry_asin")
    run_asin = c_asin2.button(
        "▶ ドライラン実行（書き込まず確認）" if dry_asin else "▶ 本番実行（ASINを書き込む）",
        key="run_asin",
        type="secondary" if dry_asin else "primary",
    )
    st.caption("⏱ 対象1件あたり約1〜2秒。ドライランで結果を確認してから本番実行できます。")

    # ドライランは即実行、本番は確認ステップを挟む
    do_asin = False
    if run_asin:
        if dry_asin:
            do_asin = True
        else:
            st.session_state["asin_confirm_needed"] = True
    _ph_asin = st.empty()
    if st.session_state.get("asin_confirm_needed") and not dry_asin:
        with _ph_asin.container():
            st.warning(f"⚠️ 【{ACCOUNT_LABELS.get(account, account)}】空のASIN欄に検索結果を実際に書き込みます。よろしいですか？")
            ca, cb = st.columns([1, 2])
            yes = ca.button("✅ はい、本番実行する", type="primary", key="run_asin_confirm")
            no = cb.button("キャンセル", key="run_asin_cancel")
        if yes:
            st.session_state["asin_confirm_needed"] = False
            _ph_asin.empty()
            do_asin = True
        elif no:
            st.session_state["asin_confirm_needed"] = False
            _ph_asin.empty()

    if do_asin:
        log_area = st.empty()
        label_asin = "ドライラン" if dry_asin else "本番"
        try:
            with st.spinner(f"ASIN 検索中 [{label_asin}]... (1件あたり約1〜2秒)"):
                lines = _stream_logs(amazon.run_asin_lookup(dry_run=dry_asin, spreadsheet_id=ss_id), log_area)
            st.success("✅ 完了しました")
            _get_status_counts.clear()
        except Exception as e:
            st.error(f"❌ エラーが発生しました（通信状況をご確認ください）: {e}")


# ── タブ3: 価格調整 ──────────────────────────────────────────
with tab3:
    st.info(
        "**価格ツールは3つあります。用途で使い分けてください（いずれもまずドライラン推奨）:**\n\n"
        "1. **💴 価格自動調整** … 同コンFBA最安値を少し下回るよう一括調整（従来方式・底値寄り）\n"
        "2. **💰 最低販売価格をそのまま反映** … AE列に手入力した価格をそのまま反映\n"
        "3. **🛒 カート連動リプライサー（おすすめ）** … カート獲得状況に応じて値上げ/奪取し利益を最大化",
        icon="🧭",
    )
    st.subheader("💴 価格自動調整")
    st.markdown("""
ステータスが **`3.出品済み`** の全商品について、**同コンディション・FBA出品者の最安値** を取得し自動調整します。

| 項目 | 内容 |
|---|---|
| 対象 | `3.出品済み` の全商品 |
| 参照価格 | 同コンディション（良い/非常に良い/可）のFBA最安値 |
| 目標価格 | 参照価格 − 引き下げ幅（円）|
| 下限 | スプレッドシートAE列（最低販売価格）。空の場合は現在価格を下限とする（=値下げしない）|
| API呼び出し | 同一ASINはキャッシュで1回のみ |
""")

    c1, c2, c3 = st.columns([1, 1, 3])
    with c1:
        dry3 = st.checkbox("ドライラン", value=True, key="dry3")
    with c2:
        step_yen = st.number_input("引き下げ幅（円）", min_value=1, max_value=1000, value=10, step=1, key="step_yen")
    run_reprice = c3.button(
        "▶ ドライラン実行" if dry3 else "▶ 本番実行",
        key="run_reprice",
        type="secondary" if dry3 else "primary",
    )
    if not dry3:
        st.warning("⚠️ 本番モード: SP-API に実際に価格変更リクエストを送信します。")

    # 本番は確認ステップを挟む（ドライランは即実行）
    do_reprice = False
    if run_reprice:
        if dry3:
            do_reprice = True
        else:
            st.session_state["confirm_reprice"] = True
    _ph_reprice = st.empty()
    if st.session_state.get("confirm_reprice") and not dry3:
        with _ph_reprice.container():
            st.warning(f"⚠️ 【{ACCOUNT_LABELS.get(account, account)}】`3.出品済み` の全商品の出品価格を実際に変更します。よろしいですか？")
            cca, ccb = st.columns([1, 2])
            yes = cca.button("✅ はい、本番実行する", type="primary", key="confirm_reprice_yes")
            no = ccb.button("キャンセル", key="confirm_reprice_no")
        if yes:
            st.session_state["confirm_reprice"] = False
            _ph_reprice.empty()
            do_reprice = True
        elif no:
            st.session_state["confirm_reprice"] = False
            _ph_reprice.empty()

    if do_reprice:
        log_area = st.empty()
        label = "ドライラン" if dry3 else "本番"
        try:
            with st.spinner(f"価格調整中 [{label}]... (1件あたり約1秒)"):
                lines = _stream_logs(
                    amazon.run_auto_reprice(dry_run=dry3, step_yen=int(step_yen), spreadsheet_id=ss_id),
                    log_area,
                )
            st.success("✅ 完了しました")
            if not dry3:
                _get_status_counts.clear()
        except Exception as e:
            st.error(f"❌ エラーが発生しました（通信状況をご確認ください）: {e}")

    st.divider()
    st.subheader("💰 最低販売価格をそのまま反映")
    st.markdown("""
AE列（最低販売価格）に入力されている価格をそのまま Amazon 出品価格に設定します。

| 項目 | 内容 |
|---|---|
| 対象 | `3.出品済み` かつ AE列に値が入っている商品 |
| 設定価格 | AE列の値をそのまま出品価格に適用 |
| スプシ更新 | J列（販売価格）も同時に書き戻す |
""")
    c4, c5 = st.columns([1, 3])
    with c4:
        dry_min = st.checkbox("ドライラン", value=True, key="dry_min")
    run_set_min = c5.button(
        "▶ ドライラン実行" if dry_min else "▶ 本番実行",
        key="run_set_min",
        type="secondary" if dry_min else "primary",
    )
    if not dry_min:
        st.warning("⚠️ 本番モード: SP-API に実際に価格変更リクエストを送信します。")

    # 本番は確認ステップを挟む（ドライランは即実行）
    do_set_min = False
    if run_set_min:
        if dry_min:
            do_set_min = True
        else:
            st.session_state["confirm_set_min"] = True
    _ph_set_min = st.empty()
    if st.session_state.get("confirm_set_min") and not dry_min:
        with _ph_set_min.container():
            st.warning(f"⚠️ 【{ACCOUNT_LABELS.get(account, account)}】AE列（最低販売価格）の値を実際の出品価格に反映します。よろしいですか？")
            cma, cmb = st.columns([1, 2])
            yes = cma.button("✅ はい、本番実行する", type="primary", key="confirm_set_min_yes")
            no = cmb.button("キャンセル", key="confirm_set_min_no")
        if yes:
            st.session_state["confirm_set_min"] = False
            _ph_set_min.empty()
            do_set_min = True
        elif no:
            st.session_state["confirm_set_min"] = False
            _ph_set_min.empty()

    if do_set_min:
        log_area2 = st.empty()
        label2 = "ドライラン" if dry_min else "本番"
        try:
            with st.spinner(f"価格反映中 [{label2}]... (1件あたり約1秒)"):
                _stream_logs(
                    amazon.run_set_price_from_min(dry_run=dry_min, spreadsheet_id=ss_id),
                    log_area2,
                )
            st.success("✅ 完了しました")
            if not dry_min:
                _get_status_counts.clear()
        except Exception as e:
            st.error(f"❌ エラーが発生しました（通信状況をご確認ください）: {e}")

    st.divider()
    st.subheader("🛒 カート連動リプライサー（NEW）")
    st.markdown("""
カート(BuyBox)の獲得状況に応じて賢い価格を計算します（サマリー起点）。

| 状況 | 計算される価格 |
|---|---|
| **カート保持中(○)** | カートを保てる範囲で**少し値上げ**（利益最大化） |
| **カート未取得・競合FBA** | カート価格を**下回って奪取** |
| **カート未取得・競合FBM** | FBAの強みで**同値〜やや上**で奪取（安売りしない） |

**自動反映 ±%** … 変更幅がこの%以内の小さい調整は**自動でAmazon反映**。
超える大きい変更は**変更金額(P列)に退避** → 価格管理ページで確認して反映（安全）。
**0にすると全件 変更金額(P列)へ**（Amazon非変更）。下限はAE列（空欄＝現在価格）。
""")
    cb1, cb2, cb3, cb4, cb5 = st.columns([1, 1, 1, 1, 1])
    with cb1:
        dry_bb = st.checkbox("ドライラン", value=True, key="dry_bb")
    with cb2:
        step_bb = st.number_input(
            "奪取きざみ(円)", min_value=1, max_value=1000, value=10, step=1, key="step_bb",
            help="カート未取得のとき、競合（カート価格やFBA最安）を何円下回るか。",
        )
    with cb3:
        raise_bb = st.number_input(
            "値上げきざみ(円)", min_value=1, max_value=5000, value=50, step=10, key="raise_bb",
            help="カート保持中に1回で何円ずつ上げるか。",
        )
    with cb4:
        prem_bb = st.number_input(
            "FBM上乗せ(%)", min_value=0, max_value=50, value=5, step=1, key="prem_bb",
            help="競合がFBMだけのとき、FBM最安より何%まで上に乗せるか。",
        )
    with cb5:
        auto_bb = st.number_input(
            "自動反映 ±%", min_value=0, max_value=50, value=10, step=1, key="auto_bb",
            help="変更幅がこの%以内なら自動でAmazon反映。超えたら変更金額(P列)へ退避。0=全件P列へ（Amazon非変更）。",
        )
    if not dry_bb:
        if auto_bb > 0:
            st.warning(f"⚠️ 変更幅 ±{int(auto_bb)}% 以内は**実際にAmazon出品価格を変更**します。超える分は変更金額(P列)へ退避。")
        else:
            st.warning("⚠️ 全件を変更金額(P列)に書き込みます（Amazonは変更しません）。")

    do_bb = st.button(
        "▶ ドライラン（どう振り分けられるか確認）" if dry_bb else "▶ 実行する",
        key="run_bb", type="secondary" if dry_bb else "primary",
    )

    if do_bb:
        log_area_bb = st.empty()
        label_bb = "ドライラン" if dry_bb else "実行"
        try:
            with st.spinner(f"カート連動リプライス計算中 [{label_bb}]... (1件あたり約1〜2秒)"):
                _stream_logs(
                    amazon.run_buybox_reprice(
                        dry_run=dry_bb, step_yen=int(step_bb), raise_step=int(raise_bb),
                        fbm_premium=float(prem_bb) / 100.0, spreadsheet_id=ss_id,
                        summary_spreadsheet_id=SUMMARY_SS_ID, auto_apply_pct=float(auto_bb),
                    ),
                    log_area_bb,
                )
            st.success("✅ 完了しました（ドライランは反映なし）。退避分は価格管理ページで確認→反映してください。")
            if not dry_bb:
                _get_status_counts.clear()
        except Exception as e:
            st.error(f"❌ エラーが発生しました（通信状況をご確認ください）: {e}")


# ── タブ4: FNSKUラベル ───────────────────────────────────────
with tab4:
    st.subheader("🏷️ FNSKUラベル生成")
    st.markdown("""
ステータスが **`3.出品済み`** の商品について、SP-API から FNSKU を取得し、
**バーコード + FNSKU + コンディション + 商品名** のラベルPDFを生成します。
**「サイズ・面付け」で1枚に1〜65ラベル**を選べます（複数選択して一括印刷もOK）。

**Google Drive の画像フォルダ構成:**
```
親フォルダ
  └── {SKU名}/   ← SKU 名のフォルダを作成して画像を入れる
        ├── 01.jpg
        └── ...（最大6枚使用）
```
""")

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        target_sku = st.text_input(
            "SKU 絞り込み（任意）",
            placeholder="例: RS00006ST… （空白=全件）",
            key="target_sku",
        )
    with c2:
        # 選択肢はここで定義（lib の _LABEL_LAYOUTS のキーと一致させること）
        _LABEL_LAYOUT_OPTIONS = [
            "1面（大・1商品1ページ）",
            "12面 (2×6)",
            "21面 (3×7)",
            "24面 (3×8)",
            "65面 (5×13)",
        ]
        label_layout = st.selectbox(
            "ラベルのサイズ・面付け",
            _LABEL_LAYOUT_OPTIONS,
            index=0,
            key="label_layout",
            help="1面=A4に1ラベル(大・切り取り)。12/24/65面=A4に複数ラベルを面付け（ラベルシール/紙節約）。",
        )
    with c3:
        st.markdown("<br>", unsafe_allow_html=True)
        run_labels = st.button("▶ PDF 生成", key="run_labels", type="primary", use_container_width=True)

    if run_labels:
        sku_filter = target_sku.strip()
        try:
            with st.spinner("PDF 生成中... (SP-API と Drive にアクセスします)"):
                pdf_bytes, logs = amazon.run_fnsku_labels(
                    target_sku=sku_filter, spreadsheet_id=ss_id, layout=label_layout)
        except Exception as e:
            st.error(f"❌ エラーが発生しました（通信状況をご確認ください）: {e}")
            pdf_bytes, logs = None, []

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

    fba_label_layout = st.selectbox(
        "FNSKUラベルのサイズ・面付け",
        ["1面（大・1商品1ページ）", "12面 (2×6)", "21面 (3×7)", "24面 (3×8)", "65面 (5×13)"],
        index=0, key="fba_label_layout",
        help="生成されるFNSKUラベルPDFの面付け。FNSKUラベルタブと同じ選択肢です。",
    )
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

    # 本番は確認ステップを挟む（ドライランは即実行）
    do_fba = False
    if run_fba:
        if dry5:
            do_fba = True
        else:
            st.session_state["confirm_fba"] = True
    _ph_fba = st.empty()
    if st.session_state.get("confirm_fba") and not dry5:
        with _ph_fba.container():
            st.warning(f"⚠️ 【{ACCOUNT_LABELS.get(account, account)}】`2.写真撮影済み` の商品を実際にAmazonへ出品登録し、納品プランを作成します。よろしいですか？")
            cfa, cfb = st.columns([1, 2])
            yes = cfa.button("✅ はい、本番実行する", type="primary", key="confirm_fba_yes")
            no = cfb.button("キャンセル", key="confirm_fba_no")
        if yes:
            st.session_state["confirm_fba"] = False
            _ph_fba.empty()
            do_fba = True
        elif no:
            st.session_state["confirm_fba"] = False
            _ph_fba.empty()

    if do_fba:
        log_area5 = st.empty()
        label = "ドライラン" if dry5 else "本番"
        try:
            with st.spinner(f"処理中 [{label}]..."):
                logs5, pdf5, plan5 = amazon.run_fba_inbound(
                    account_name=account, dry_run=dry5, spreadsheet_id=ss_id,
                    label_layout=fba_label_layout,
                )
        except Exception as e:
            st.error(f"❌ エラーが発生しました（通信状況をご確認ください）: {e}")
            logs5, pdf5, plan5 = [], None, None

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
    st.markdown("#### ② 輸送方法の選択・確定（⭐Amazon提携配送を既定に）")
    st.info(
        "**Amazon提携配送（日本郵便）を既定**にしました。割引運賃で、**Amazonが集荷を手配**します。\n\n"
        "「▶ 輸送オプションを取得」→ ⭐提携配送（小口）が先頭・既定選択で表示されます。"
        "金額(JPY)を見てヤマト/佐川より安ければそのまま確定。自社手配が良ければ下の選択肢から選べます。\n\n"
        "※ SP-API の `inbound_shipment_transport_write` スコープが必要です（未取得なら403。管理者に申請）。",
        icon="🚚",
    )

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

    # 発送予定日（readyToShipWindow.start）入力
    # 提携配送ではこの日が「荷物が出せる＝集荷してもらう日」の基準になります。
    from datetime import date, timedelta
    c_date1, c_date2 = st.columns(2)
    with c_date1:
        ship_start_date = st.date_input(
            "発送予定日（集荷希望日）", value=date.today() + timedelta(days=1), key="fba_ship_start",
            help="荷物が出荷できる日。Amazon提携配送ではこの日を基準に集荷が手配されます。",
        )
    with c_date2:
        ship_end_date = st.date_input(
            "発送可能 終了日（自社手配の目安）", value=date.today() + timedelta(days=14), key="fba_ship_end",
            help="自社手配(ヤマト/佐川)を選ぶ場合の発送可能期間の終わり。提携配送では使いません。",
        )

    date_invalid = ship_start_date > ship_end_date
    if date_invalid:
        st.error("発送予定日が終了日より後になっています。日付を見直してください。")

    if not _plan_id:
        st.caption("💡 ① を実行するとプランIDが自動入力されます。または上の欄に手動で入力してください。")
    get_transport = st.button(
        "▶ 輸送オプションを取得",
        key="get_transport",
        type="primary",
        disabled=not _plan_id or date_invalid,
    )

    if get_transport:
        if not _plan_id or not _placement_id or not _shipment_ids:
            st.error("プランID・配置オプションID・シップメントIDをすべて入力してください（① を先に実行してください）。")
        else:
            ship_start_iso = f"{ship_start_date.isoformat()}T00:00:00Z"
            ship_end_iso = f"{ship_end_date.isoformat()}T23:59:59Z"
            with st.spinner("輸送オプションを取得中..."):
                result = amazon.get_fba_transportation_options(
                    account_name=account,
                    plan_id=_plan_id,
                    placement_option_id=_placement_id,
                    shipment_ids=_shipment_ids,
                    ready_to_ship_start=ship_start_iso,
                    ready_to_ship_end=ship_end_iso,
                )
            if result["error"]:
                st.error(f"エラー: {result['error']}")
            elif not result["options"]:
                st.warning(
                    "輸送オプションが見つかりませんでした。\n\n"
                    "**小口発送（ヤマト・佐川）の場合**: このステップは不要です。"
                    "① のログに表示された発送先FC住所へ直接発送してください。"
                )
            else:
                all_have_precond = all(o.get("preconditions") for o in result["options"])
                st.session_state["fba_transport_options"] = result["options"]
                if all_have_precond:
                    st.warning(
                        f"⚠️ {len(result['options'])} 件取得しましたが、すべてに事前設定（配送枠確認など）が必要です。\n\n"
                        "**小口発送（ヤマト・佐川）の場合**: このステップをスキップして、"
                        "① のログの発送先FC住所へ直接発送してください。\n\n"
                        "**大口貨物（LTL）の場合**: Seller Central から輸送方法を選択してください。"
                    )
                else:
                    st.success(f"✅ {len(result['options'])} 件の輸送オプションを取得しました")

    # オプション選択 → 確定
    transport_options = st.session_state.get("fba_transport_options", [])
    if transport_options:
        st.markdown("**輸送方法を選択してください**（⭐ = Amazon提携配送・おすすめ）")
        st.caption("⭐ Amazon提携配送（日本郵便）は割引運賃＋Amazonが集荷を手配。"
                   "金額(JPY)を見てヤマト/佐川より安ければ提携がおすすめです。")

        def _solution_jp(sol):
            return {"AMAZON_PARTNERED_CARRIER": "Amazon提携配送",
                    "USE_YOUR_OWN_CARRIER": "自社手配（ヤマト/佐川）"}.get(sol, sol or "不明")

        def _mode_jp(mode):
            return {"GROUND_SMALL_PARCEL": "小口", "FREIGHT_LTL": "大口(LTL)",
                    "AIR_SMALL_PARCEL": "航空小口"}.get(mode, mode or "")

        def _is_partnered_spd(opt):
            return (opt.get("shippingSolution") == "AMAZON_PARTNERED_CARRIER"
                    and opt.get("shippingMode") == "GROUND_SMALL_PARCEL")

        def _option_label(opt):
            sol = opt.get("shippingSolution", "")
            mode = opt.get("shippingMode", "")
            amount = opt.get("quote", {}).get("cost", {}).get("amount", 0)
            star = "⭐ " if _is_partnered_spd(opt) else ""
            label = f"{star}{_solution_jp(sol)}・{_mode_jp(mode)}"
            carrier = opt.get("carrier", {}).get("name", "")
            if carrier and carrier != "Other":
                label += f" [{carrier}]"
            if amount:
                label += f" — JPY {amount:,.0f}"
            if opt.get("preconditions"):
                label += " ⚠️要事前設定"
            return label

        option_labels = [_option_label(o) for o in transport_options]
        # 提携小口(⭐)があればそれを既定選択に
        default_idx = next((i for i, o in enumerate(transport_options) if _is_partnered_spd(o)), 0)

        selected_label = st.radio("輸送オプション", option_labels, index=default_idx,
                                  key="fba_transport_radio")
        selected_idx = option_labels.index(selected_label)
        selected_opt = transport_options[selected_idx]

        # 選択したオプションの preconditions 表示
        sel_pre = selected_opt.get("preconditions", [])
        if sel_pre:
            st.warning(
                f"⚠️ このオプションは事前設定が必要なため確定できません: **{', '.join(sel_pre)}**\n\n"
                "小口発送（ヤマト・佐川）の場合は ① のログの発送先FC住所へ直接発送してください。\n"
                "大口貨物の場合は Seller Central から輸送方法を選択してください。"
            )

        confirm_transport = st.button("▶ 輸送方法を確定する", key="confirm_transport", type="primary",
                                      disabled=bool(sel_pre))
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
        try:
            with st.spinner("Amazon の受取状況を確認中..."):
                lines_r = _stream_logs(amazon.run_receipt_check(spreadsheet_id=ss_id), log_area_r)
            st.success("✅ 完了しました")
            _get_status_counts.clear()
        except Exception as e:
            st.error(f"❌ エラーが発生しました（通信状況をご確認ください）: {e}")


# ── タブ6: 商品サマリー ──────────────────────────────────────

with tab6:
    st.subheader("📊 商品サマリー")
    st.markdown(
        f"出力先: **[商品一覧スプレッドシート]({SUMMARY_SS_URL})**"
    )

    # ── 🛒 カート獲得ダッシュボード（D）──────────────────────
    st.markdown("### 🛒 カート獲得ダッシュボード")

    @st.cache_data(ttl=120)
    def _load_cart_dashboard():
        return amazon.get_cart_dashboard_data(SUMMARY_SS_ID)

    cda, cdb = st.columns([3, 1])
    with cdb:
        if st.button("🔄 今すぐ更新", key="dash_refresh", use_container_width=True,
                     help="最新のサマリーをAmazonから取得して反映します（数分）"):
            log_dash = st.empty()
            lines_d = []
            try:
                with st.spinner("Amazonから最新データを取得中...（数分）"):
                    for msg in amazon.run_create_summary_sheet(SUMMARY_SS_ID):
                        lines_d.append(msg)
                        log_dash.markdown(
                            '<div class="log-box">' + "\n".join(lines_d[-30:]) + "</div>",
                            unsafe_allow_html=True)
                _load_cart_dashboard.clear()
                st.success("✅ 更新しました")
            except Exception as e:
                st.error(f"❌ 取得に失敗しました: {e}")

    # ── ⚡ カート最適化（1クリック実行）──
    with st.container(border=True):
        oc1, oc2, oc3 = st.columns([2, 1, 1])
        oc1.markdown("**⚡ カート最適化**\nカートロジックで価格を計算して最適化します。")
        opt_pct = oc2.number_input("自動反映 ±%", min_value=0, max_value=50, value=10, step=1,
                                   key="opt_pct",
                                   help="この%以内の小さい変更は自動でAmazon反映。超える分は変更金額(P列)へ退避し価格管理ページで確認。0=全件P列へ。")
        if oc3.button("▶ 最適化を実行", key="run_opt", type="primary", use_container_width=True):
            st.session_state["confirm_opt"] = True
        if st.session_state.get("confirm_opt"):
            if opt_pct > 0:
                st.warning(f"⚠️ 変更幅 ±{int(opt_pct)}% 以内は**実際にAmazon価格を変更**します（超える分は変更金額へ退避）。実行しますか？")
            else:
                st.warning("⚠️ 全件を変更金額(P列)に書き込みます（Amazonは変更しません）。実行しますか？")
            o1, o2 = st.columns([1, 2])
            if o1.button("✅ はい、実行", type="primary", key="confirm_opt_yes"):
                st.session_state["confirm_opt"] = False
                log_opt = st.empty()
                try:
                    with st.spinner("カート最適化を実行中...（1件あたり約1〜2秒）"):
                        _stream_logs(
                            amazon.run_buybox_reprice(
                                dry_run=False, summary_spreadsheet_id=SUMMARY_SS_ID,
                                auto_apply_pct=float(opt_pct),
                            ),
                            log_opt,
                        )
                    _load_cart_dashboard.clear()
                    st.success("✅ 完了。退避分があれば価格管理ページで確認→反映してください。")
                except Exception as e:
                    st.error(f"❌ エラー: {e}")
            if o2.button("キャンセル", key="confirm_opt_no"):
                st.session_state["confirm_opt"] = False

    try:
        dash = _load_cart_dashboard()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("カート獲得率", f"{dash['rate']}%")
        m2.metric("○ 獲得", dash["won"])
        m3.metric("△ 新品優先", dash["partial"])
        m4.metric("✗ 未獲得", dash["lost"])

        if dash["near"]:
            st.markdown("**🎯 あと一歩でカート獲得（✗のうち、カート価格との差が小さい順）**")
            st.caption("「差額」だけ値下げすればカート獲得が狙えます。価格管理ページで調整できます。")
            import pandas as _pd
            near_df = _pd.DataFrame([{
                "商品名": n["name"],
                "現在価格": f"¥{n['price']:,}",
                "カート価格": f"¥{n['cart']:,}",
                "差額": f"¥{n['gap']:,}",
            } for n in dash["near"]])
            st.dataframe(near_df, hide_index=True, use_container_width=True)
        else:
            st.info("「あと一歩」のSKUはありません（✗かつカート価格より高い商品なし）。")

        if dash["history"] and len(dash["history"]) >= 2:
            import pandas as _pd
            _hist_cols = ["日時", "対象数", "○獲得", "△", "✗未獲得", "獲得率%"]
            hist_df = _pd.DataFrame(dash["history"], columns=_hist_cols)
            try:
                hist_df["獲得率%"] = _pd.to_numeric(hist_df["獲得率%"], errors="coerce")
                st.markdown("**📈 獲得率の推移**")
                st.line_chart(hist_df.set_index("日時")["獲得率%"], height=200)
            except Exception:
                pass
        else:
            st.caption("📈 推移グラフは履歴が2回分たまると表示されます（毎時更新で蓄積）。")
    except Exception as e:
        st.warning(f"ダッシュボードの読み込みに失敗しました: {e}")

    st.divider()

    # ── ① サマリー更新 ──────────────────────────────────────
    st.markdown("### ① サマリー更新")
    st.markdown("""
FBA在庫・注文履歴・競合価格・カート獲得状況をAmazon APIから取得して一覧を更新します。

| 情報 | 取得元 |
|---|---|
| ステータス・商品名・写真 | FBA Inventory / CatalogItems API |
| 販売価格・カート価格 | Products Pricing API |
| カート獲得（○/△/✗） | get_listings_offer |
| 仕入れ値・最低販売価格 | 管理スプレッドシート |
""")
    run_summary = st.button("▶ サマリーを更新", key="run_summary", type="primary")
    st.caption("⏱ 商品数に応じて数分かかります（1商品あたり約2.5秒）")

    if run_summary:
        log_area6 = st.empty()
        lines6 = []
        try:
            with st.spinner("サマリー更新中..."):
                for msg in amazon.run_create_summary_sheet(SUMMARY_SS_ID):
                    lines6.append(msg)
                    log_area6.markdown(
                        '<div class="log-box">' + "\n".join(lines6[-80:]) + "</div>",
                        unsafe_allow_html=True,
                    )
        except Exception as e:
            st.error(f"❌ エラーが発生しました: {e}")
            import traceback
            st.code(traceback.format_exc(), language="python")
        else:
            st.success("✅ 更新完了")
            st.markdown(f"**[📊 スプレッドシートを開く]({SUMMARY_SS_URL})**")

    st.divider()

    # ── ② P列（変更金額）をAmazonに反映 ─────────────────
    st.markdown("### ② 変更金額をAmazonに反映")
    st.markdown("""
サマリーシートの **P列（変更金額）** に入力した価格を Amazon 出品価格に設定します。
（価格管理ページで入力した変更金額もこのP列に入ります）

| 項目 | 内容 |
|---|---|
| 対象 | ステータスが `販売中` または `納品中` かつ P列（変更金額）に値がある行 |
| 変更後 | Amazon 出品価格・サマリーG列・管理シートJ列をすべて更新 |
""")
    c6a, c6b = st.columns([1, 3])
    with c6a:
        dry6 = st.checkbox("ドライラン", value=True, key="dry6")
    run_set_price = c6b.button(
        "▶ ドライラン確認" if dry6 else "▶ 本番反映",
        key="run_set_price",
        type="secondary" if dry6 else "primary",
    )
    if not dry6:
        st.warning("⚠️ 本番モード: Amazon 出品価格を実際に変更します。")

    # 本番は確認ステップを挟む（ドライランは即実行）
    do_set_price = False
    if run_set_price:
        if dry6:
            do_set_price = True
        else:
            st.session_state["confirm_set_price"] = True
    _ph_set_price = st.empty()
    if st.session_state.get("confirm_set_price") and not dry6:
        with _ph_set_price.container():
            st.warning(f"⚠️ 【{ACCOUNT_LABELS.get(account, account)}】サマリーP列（変更金額）の値をAmazon出品価格に実際に反映します。よろしいですか？")
            c6c, c6d = st.columns([1, 2])
            yes = c6c.button("✅ はい、本番反映する", type="primary", key="confirm_set_price_yes")
            no = c6d.button("キャンセル", key="confirm_set_price_no")
        if yes:
            st.session_state["confirm_set_price"] = False
            _ph_set_price.empty()
            do_set_price = True
        elif no:
            st.session_state["confirm_set_price"] = False
            _ph_set_price.empty()

    if do_set_price:
        log_area6b = st.empty()
        try:
            label6 = "ドライラン" if dry6 else "本番"
            with st.spinner(f"価格反映中 [{label6}]..."):
                _stream_logs(
                    amazon.run_set_price_from_summary(
                        dry_run=dry6,
                        summary_spreadsheet_id=SUMMARY_SS_ID,
                        management_spreadsheet_id=ss_id,
                    ),
                    log_area6b,
                )
        except Exception as e:
            st.error(f"❌ エラーが発生しました: {e}")
            import traceback
            st.code(traceback.format_exc(), language="python")
        else:
            st.success("✅ 完了しました")
