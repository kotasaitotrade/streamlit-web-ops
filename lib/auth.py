"""認証ヘルパ。Streamlit 1.42+ の st.login() を使う。"""
from __future__ import annotations

import streamlit as st

from .sheets import get_user, user_is_active


def _bypass_email() -> str | None:
    """secrets の [bypass] enable=true なら指定 email を返す。
    テスト/E2E 用。本番では secrets から消すこと。
    bypass モード中は ?as_user=<email> で別ペルソナに切替可能。"""
    try:
        if "bypass" not in st.secrets:
            return None
        bp = st.secrets["bypass"]
        enable = bp.get("enable") if hasattr(bp, "get") else None
        if not enable:
            return None
        # URL クエリで上書き（bypass enable 時のみ有効）
        try:
            as_user = st.query_params.get("as_user")
            if as_user:
                return str(as_user)
        except Exception:
            pass
        email = bp.get("email") if hasattr(bp, "get") else None
        if email:
            return str(email)
    except Exception:
        pass
    return None


def current_user_email() -> str | None:
    """ログイン中ユーザーの email を返す（未ログインなら None）。"""
    bypass = _bypass_email()
    if bypass:
        return bypass
    if not hasattr(st, "user") or not st.user.is_logged_in:
        return None
    return getattr(st.user, "email", None)


def require_login(provider: str = "google") -> dict | None:
    """ログイン必須ガード。users シート照合まで含めて済ます。
    返り値: 認可済みユーザー行 (dict) または None（ボタンを表示して停止）。"""

    # ── テスト用バイパス ──
    bypass = _bypass_email()
    if bypass:
        st.sidebar.warning(f"🧪 bypass mode: {bypass}")
        user = get_user(bypass)
        if not user_is_active(user):
            st.error(f"bypass email '{bypass}' が users シートに登録されていないか active=FALSE です")
            st.stop()
        return user

    if not hasattr(st, "login"):
        st.error("この Streamlit には st.login() がありません。1.42 以上にアップグレードしてください。")
        st.stop()

    if not st.user.is_logged_in:
        st.title("🔐 Web Ops")
        st.write("Google アカウントでログインしてください。")
        if st.button("Google でログイン", type="primary"):
            st.login(provider)
        st.stop()

    email = st.user.email
    user = get_user(email)
    if not user_is_active(user):
        st.title("⛔ 認可されていません")
        st.write(f"ログイン: **{email}**")
        st.write("このメールアドレスは登録されていないか、無効化されています。管理者に連絡してください。")
        if st.button("ログアウト"):
            st.logout()
        st.stop()

    return user


def logout_button():
    if _bypass_email():
        return
    if st.user.is_logged_in and st.sidebar.button("🚪 ログアウト"):
        st.logout()
