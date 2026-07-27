"""Google Drive へ画像を上げて公開URL(hotlink)を返す。

なぜ：Threads公式APIは「公開URLの画像」しか貼れない（端末内の写真を直接添付は不可）。
実写真を Drive に上げ、anyone/reader で公開し、その直リンクを投稿の image_url に渡す。

資格情報：gspread と同じ OAuth トークン(lib.sheets の TOKEN_PATH / SCOPES)を流用する。
SCOPES には drive が含まれているため追加設定は不要。

★実装メモ（2026-07-27）：以前は googleapiclient(build/MediaIoBaseUpload)を使っていたが、
　Streamlit Cloud の新しいPython環境で googleapiclient が import できず ModuleNotFoundError で落ちた。
　gspread が使っている google-auth の AuthorizedSession + Drive REST API(requests) に置き換え、
　重い googleapiclient 依存を外した（gspread が動く＝この認証系は必ず入っている）。

URL形式：lh3.googleusercontent.com/d/{id} を使う（200を直接返す・iframe表示も可）。
uc?export=view は大容量でウイルススキャン警告ページになり得るため使わない。
"""
from __future__ import annotations

import io
import json

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from lib.sheets import SCOPES, TOKEN_PATH

_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id"
_PERM_URL = "https://www.googleapis.com/drive/v3/files/{fid}/permissions?fields=id"


def _session() -> AuthorizedSession:
    """gspread と同じトークンで認証済みの requests セッションを返す（googleapiclient不要）。"""
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return AuthorizedSession(creds)


def _prepare(data: bytes) -> bytes:
    """長辺1600pxに縮小＋EXIF除去してJPEG化（容量削減・位置情報等の個人情報除去）。
    Pillowが無い等で失敗したら原本をそのまま返す。"""
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)   # 撮影時の回転を反映してからEXIFを捨てる
        im = im.convert("RGB")
        im.thumbnail((1600, 1600))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return data


def upload_image_public(file_name: str, data: bytes, folder_id: str | None = None) -> str:
    """画像バイトを Drive へ上げ、anyone/reader 公開し、hotlink 可能な公開URL(lh3)を返す。
    実物・本人撮影のみを渡すこと（拾い画NG）。"""
    jpg = _prepare(data)
    base = (file_name.rsplit(".", 1)[0] or "chi_photo")
    meta: dict = {"name": f"{base}.jpg"}
    if folder_id:
        meta["parents"] = [folder_id]

    sess = _session()

    # Drive の multipart/related アップロード（メタJSON + 画像バイト）を手組みする。
    boundary = "chiphoto_boundary_2f8c1e"
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(meta)}\r\n"
        f"--{boundary}\r\n"
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8") + jpg + f"\r\n--{boundary}--\r\n".encode("utf-8")

    r = sess.post(_UPLOAD_URL, data=body,
                  headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                  timeout=60)
    r.raise_for_status()
    fid = r.json()["id"]

    # anyone/reader で公開（hotlink 可能に）
    sess.post(_PERM_URL.format(fid=fid),
              json={"type": "anyone", "role": "reader"}, timeout=30)

    return f"https://lh3.googleusercontent.com/d/{fid}"
