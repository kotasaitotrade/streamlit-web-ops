"""撮影・出品入力ページ。

外注さん（作業者）が自分の「商品管理」シートへ撮影・出品データを入力する。
管理者(role=admin)は作業者を選んで同じ UI で入力・新規行追加ができる。
仕入れデータ入力は自動化済みのため本ページの対象外。
"""
import streamlit as st

from lib.sheets import materialize_secrets
from lib.auth import require_login, logout_button
import lib.syuppin as sp

materialize_secrets()

user = require_login()
logout_button()

st.title("📸 撮影・出品入力")


def selectbox_config(header):
    help_ = sp.COL_HELP.get(header)
    opts = {
        "ステータス": sp.STATUS_OPTIONS, "状態": sp.CONDITION_OPTIONS,
        "状態-撮影": sp.AMAZON_CONDITION_OPTIONS, "計算用": sp.KEISAN_OPTIONS,
        "出品サイト": sp.SITE_OPTIONS,
    }.get(header)
    if opts is None:
        return None
    width = "medium" if header in ("ステータス", "状態") else "small"
    return st.column_config.SelectboxColumn(header, options=opts, width=width, help=help_)


def render_editor(spreadsheet_id, worker_key, manager):
    with st.expander("📖 入力のしかた（はじめての方はこちら）", expanded=False):
        st.markdown(
            "1. 下の表から、撮影・出品する商品の行を探します（**🔍検索**で型番や管理IDから探せます）\n"
            "2. **型番など・状態・販売価格** など、分かる項目を入力します\n"
            "3. 撮影・出品が終わったら **ステータス** を「**2.写真撮影済み**」にします\n"
            "   → **撮影日は自動で入ります**（手入力不要）\n"
            "4. 最後に **💾変更を保存** ボタンを押します（押すまで保存されません）\n\n"
            "※ 表は右にスクロールできます。参照用の仕入れ情報は右上の「仕入れ情報も表示」で出せます。\n"
            "※ 撮影写真は、ドライブ等にアップして共有URLを『画像』欄に貼り付けます（仕入れ元URLが自動で入っていればそのままでOK）。"
        )

    headers, rows = sp.read_sheet(spreadsheet_id)
    if not headers:
        st.warning("シートが空です。")
        return
    full_df = sp.build_dataframe(headers, rows)

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        query = st.text_input("🔍 検索（管理ID・型番・仕入れ元）", key=f"q_{worker_key}",
                              placeholder="例: RS00123 / Switch / メルカリ")
    with c2:
        show_all = st.toggle("全件表示", value=False, help="オフだと『撮影待ち』の行だけ表示します")
        show_ref = st.toggle("仕入れ情報も表示", value=False, help="仕入れ値・仕入れ日・仕入れ元などの参照列を表示します")
    with c3:
        if st.button("🔄 最新を再読み込み", use_container_width=True):
            st.rerun()

    if not show_all and "ステータス" in full_df.columns:
        view_df = full_df[full_df["ステータス"].isin(sp.PRE_SHOOT_STATUSES)].copy()
    else:
        view_df = full_df.copy()

    if query:
        q = query.strip().lower()
        cols = [c for c in ["管理ID", "型番など", "仕入れ元", "備考"] if c in view_df.columns]
        mask = None
        for c in cols:
            m = view_df[c].astype(str).str.lower().str.contains(q, na=False)
            mask = m if mask is None else (mask | m)
        if mask is not None:
            view_df = view_df[mask]

    st.caption(f"対象シート: **{worker_key}** / 「{sp.SHEET_NAME}」 ・ 全 {len(full_df)} 行中 **{len(view_df)} 行**表示")
    if view_df.empty:
        st.info("表示できる行がありません。検索条件を変えるか、『全件表示』をオンにしてください。")
        return

    editable = [c for c in sp.EDITABLE_COLS if c in headers]
    id_cols = [c for c in sp.ID_COLS if c in headers]
    ref_cols = [c for c in sp.READONLY_COLS if c in headers] if show_ref else []
    display_cols = id_cols + editable + ref_cols
    display_df = view_df[display_cols].copy()
    for c in display_cols:
        if c in sp.SELECT_COLS:
            display_df[c] = display_df[c].replace("", None)
    row_numbers = view_df["__row"].tolist()
    shoot_dates = view_df["撮影日"].tolist() if "撮影日" in view_df.columns else [""] * len(view_df)

    col_cfg = {}
    for c in id_cols:
        col_cfg[c] = st.column_config.TextColumn(c, disabled=True, help="商品の管理番号（編集できません）")
    for c in ref_cols:
        col_cfg[c] = st.column_config.TextColumn(c, disabled=True, help="参照用（編集できません）")
    for c in editable:
        sc = selectbox_config(c)
        col_cfg[c] = sc if sc else st.column_config.TextColumn(c, help=sp.COL_HELP.get(c))

    edited = st.data_editor(
        display_df, use_container_width=True, hide_index=True,
        num_rows="fixed", column_config=col_cfg, key=f"editor_{worker_key}",
    )
    st.caption("💡 選択欄の「None」は未入力（空欄）の意味です。クリックすると一覧から選べます。表は横スクロールできます。")

    if st.button("💾 変更を保存", type="primary"):
        updates, changed = sp.diff_updates(headers, display_df, edited, row_numbers, shoot_dates)
        if not updates:
            st.info("変更はありませんでした。")
        else:
            try:
                n = sp.apply_updates(spreadsheet_id, updates)
                st.success(f"✅ {changed} 行 / {n} セルを保存しました。")
                st.rerun()
            except Exception as e:
                st.error(f"保存エラー: {e}")

    if manager:
        with st.expander("➕ 新規行を追加（管理者）"):
            with st.form(f"add_{worker_key}"):
                cols = st.columns(3)
                rec = {}
                targets = [c for c in ["型番など", "販売価格", "仕入れ値", "仕入れ元", "ステータス", "状態", "計算用", "管理者"] if c in headers]
                for i, c in enumerate(targets):
                    with cols[i % 3]:
                        if c == "ステータス":
                            rec[c] = st.selectbox(c, sp.STATUS_OPTIONS, key=f"a_{worker_key}_{c}")
                        elif c == "状態":
                            rec[c] = st.selectbox(c, sp.CONDITION_OPTIONS, key=f"a_{worker_key}_{c}")
                        elif c == "計算用":
                            rec[c] = st.selectbox(c, sp.KEISAN_OPTIONS, key=f"a_{worker_key}_{c}")
                        else:
                            rec[c] = st.text_input(c, key=f"a_{worker_key}_{c}")
                if st.form_submit_button("この内容で追加"):
                    try:
                        if rec.get("ステータス") == sp.DONE_SHOOT_STATUS and "撮影日" in headers and not rec.get("撮影日"):
                            rec["撮影日"] = sp.today_jst()
                        sp.append_row(spreadsheet_id, headers, rec)
                        st.success("✅ 1 行追加しました。")
                        st.rerun()
                    except Exception as e:
                        st.error(f"追加エラー: {e}")


# ── 対象作業者の決定 ──
if sp.is_manager(user):
    options = list(sp.SPREADSHEET_IDS.keys())
    worker_key = st.selectbox("入力する作業者を選択", options,
                              format_func=lambda k: sp.KEY_LABEL.get(k, k))
    render_editor(sp.SPREADSHEET_IDS[worker_key], worker_key, manager=True)
else:
    worker_key = sp.resolve_worker_key(user)
    if not worker_key:
        st.warning(
            "あなたの担当（作業者）が設定されていません。\n\n"
            "管理者に、users シートのあなたの行へ担当キー（例: kaho）を "
            "`worker_key` 列で設定してもらってください。"
        )
    else:
        render_editor(sp.SPREADSHEET_IDS[worker_key], worker_key, manager=False)
