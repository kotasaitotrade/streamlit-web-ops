"""認証ヘルパ。Streamlit 1.42+ の st.login() を使う。"""
from __future__ import annotations

import streamlit as st

from .sheets import get_user, user_is_active


def current_user_email() -> str | None:
    """ログイン中ユーザーの email を返す（未ログインなら None）。"""
    if not hasattr(st, "user") or not st.user.is_logged_in:
        return None
    return getattr(st.user, "email", None)


def require_login(provider: str = "google") -> dict | None:
    """ログイン必須ガード。users シート照合まで含めて済ます。
    返り値: 認可済みユーザー行 (dict) または None（ボタンを表示して停止）。"""
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
    if st.user.is_logged_in and st.sidebar.button("🚪 ログアウト"):
        st.logout()
