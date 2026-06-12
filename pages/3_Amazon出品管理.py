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
st.caption("ASIN自動取得 / 自動出品 / 価格調整 / FNSKUラベル生成")


# ============================================================
# サイドバー: スプレッドシート概要
# ============================================================
@st.cache_data(ttl=60)
def _get_status_counts():
    try:
        return amazon.get_status_counts(), None
    except Exception as e:
        return {}, str(e)


def _show_sidebar():
    counts, err = _get_status_counts()
    if err:
        st.sidebar.warning(f"スプシ読み込みエラー: {err[:60]}")
        return
    st.sidebar.markdown("### 📊 スプレッドシート状況")
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


_show_sidebar()


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
tab1, tab2, tab3, tab4 = st.tabs([
    "🔍 ASIN取得",
    "📤 自動出品",
    "💴 価格調整",
    "🏷️ FNSKUラベル",
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
        log_area = st.empty()
        with st.spinner("ASIN 検索中... (1件あたり約1秒)"):
            lines = _stream_logs(amazon.run_asin_lookup(dry_run=False), log_area)
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
            lines = _stream_logs(amazon.run_auto_listing(dry_run=dry2), log_area)
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
            lines = _stream_logs(amazon.run_auto_reprice(dry_run=dry3), log_area)
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
            pdf_bytes, logs = amazon.run_fnsku_labels(target_sku=sku_filter)

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
