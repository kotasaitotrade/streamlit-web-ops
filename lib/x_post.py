"""X(Twitter) 投稿ヘルパ。OAuth1.0a でツイート＋画像アップロード。

認証情報は st.secrets['x_api']（consumer_key / consumer_secret /
access_token / access_token_secret）から読む。ローカル実行時は
system_trade の config/x_api.json をフォールバックで使う。
"""
from __future__ import annotations

import json
import os

import streamlit as st
from requests_oauthlib import OAuth1Session

TWEETS_ENDPOINT = "https://api.x.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"
_KEYS = ("consumer_key", "consumer_secret", "access_token", "access_token_secret")
# ローカル実行時に画像パスを解決するための system_trade ルート候補
_LOCAL_ROOTS = ("/Users/user/git/system_trade",)


def _creds() -> dict:
    try:
        if "x_api" in st.secrets:
            s = st.secrets["x_api"]
            return {k: s[k] for k in _KEYS}
    except Exception:
        pass
    for p in ("config/x_api.json", *(os.path.join(r, "config", "x_api.json") for r in _LOCAL_ROOTS)):
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
            return {k: d[k] for k in _KEYS}
    raise RuntimeError("X APIの認証情報がありません（secrets[x_api] を設定してください）")


def has_credentials() -> bool:
    try:
        _creds()
        return True
    except Exception:
        return False


def _oauth() -> OAuth1Session:
    c = _creds()
    return OAuth1Session(
        c["consumer_key"], client_secret=c["consumer_secret"],
        resource_owner_key=c["access_token"], resource_owner_secret=c["access_token_secret"])


def resolve_image(path: str) -> str:
    """x_posts の image 列（system_trade 相対パス）をローカルの実ファイルへ解決。無ければ""。"""
    if not path or not str(path).strip():
        return ""
    path = str(path).strip()
    cands = [path]
    if not os.path.isabs(path):
        cands += [os.path.join(r, path) for r in _LOCAL_ROOTS]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return ""


def upload_media(image_path: str) -> str:
    with open(image_path, "rb") as f:
        r = _oauth().post(MEDIA_UPLOAD_ENDPOINT, files={"media": f.read()})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"media/upload {r.status_code}: {r.text[:200]}")
    return r.json().get("media_id_string", "")


def weighted_len(text: str) -> int:
    """Xの重み付き文字数（日本語1文字=2）。上限280。"""
    return sum(2 if ord(c) > 0x2000 else 1 for c in text)


def post_tweet(text: str, image_path: str = "") -> str:
    """ツイート投稿（画像が解決できれば添付）。成功で tweet_id を返す。"""
    payload = {"text": text}
    img = resolve_image(image_path)
    if img:
        mid = upload_media(img)
        if mid:
            payload["media"] = {"media_ids": [mid]}
    r = _oauth().post(TWEETS_ENDPOINT, json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"X API {r.status_code}: {r.text[:300]}")
    return r.json().get("data", {}).get("id", "")
