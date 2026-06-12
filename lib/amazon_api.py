"""
Amazon せどりツール ロジック層。
Streamlit Cloud の st.secrets から認証情報を読み込んで動作する。

必要な secrets セクション:
  [sp_api]
  lwa_app_id      = "amzn1.application-oa2-client.xxx"
  lwa_client_secret = "amzn1.oa2-cs.v1.xxx"
  refresh_token   = "Atzr|xxx"
  seller_id       = "AZENWJXLQ660S"
  marketplace_id  = "A1VC38T7YXB528"

  [amazon_config]
  spreadsheet_id       = "1Xb66vv997dWX9CIofuPNY23tuIQwoNFmm-hNBLbnBYo"
  drive_image_folder_id = "1Mszsvbh9kSh1br_HB3fHIIkhdPVC0092"
"""
from __future__ import annotations

import io
import math
import time
import warnings
warnings.filterwarnings("ignore")

import streamlit as st

# ============================================================
# 認証ヘルパー
# ============================================================

def _sp_creds() -> dict:
    sec = st.secrets["sp_api"]
    return {
        "lwa_app_id":       sec["lwa_app_id"],
        "lwa_client_secret": sec["lwa_client_secret"],
        "refresh_token":    sec["refresh_token"],
    }

def _seller_id() -> str:
    return st.secrets["sp_api"]["seller_id"]

def _marketplace_id() -> str:
    return st.secrets["sp_api"]["marketplace_id"]

def _spreadsheet_id() -> str:
    return st.secrets["amazon_config"]["spreadsheet_id"]


def _ssid(sid=None) -> str:
    """spreadsheet_id 引数が渡されていればそれを使い、なければ secrets のデフォルト値を使う。"""
    return sid if sid else _spreadsheet_id()

def _drive_folder_id() -> str:
    return st.secrets["amazon_config"]["drive_image_folder_id"]


_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
_TOKEN_PATH = "gspread_token.json"


def _google_creds():
    """gspread_token.json から認証情報を返す。期限切れなら refresh してファイルに書き戻す。
    lib/sheets.py の get_client() と同じパターン。materialize_secrets() 後に呼ぶこと。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    creds = Credentials.from_authorized_user_file(_TOKEN_PATH, _GOOGLE_SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _sheets_service():
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=_google_creds(), cache_discovery=False)


def _drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_google_creds(), cache_discovery=False)


# ============================================================
# スプレッドシート
# ============================================================

SHEET_NAME = "商品管理"

COL_KANRI_ID = 0
COL_STATUS   = 3
COL_SHIIRE   = 4
COL_HANBAI   = 9
COL_SKU      = 14
COL_ASIN     = 15
COL_STATE    = 17
COL_NOTE_VG  = 18
COL_NOTE_G   = 19


def _cell(row: list, col: int) -> str:
    return row[col].strip() if col < len(row) and row[col] else ""


def _read_rows(col_end: str = "T", spreadsheet_id=None) -> list[tuple[int, list]]:
    """(sheet_row, row_data) のリストを返す。sheet_row は 1-indexed。"""
    result = _sheets_service().spreadsheets().values().get(
        spreadsheetId=_ssid(spreadsheet_id),
        range=f"{SHEET_NAME}!A2:{col_end}200",
    ).execute()
    rows = result.get("values", [])
    return [(i + 2, row) for i, row in enumerate(rows)]


def get_status_counts(spreadsheet_id=None) -> dict:
    """ステータス別件数を返す（サイドバー表示用）。"""
    from collections import Counter
    result = _sheets_service().spreadsheets().values().get(
        spreadsheetId=_ssid(spreadsheet_id),
        range=f"{SHEET_NAME}!D2:D300",
    ).execute()
    rows = result.get("values", [])
    return dict(Counter(r[0].strip() for r in rows if r))


def _update_cell(row: int, col_letter: str, value: str, spreadsheet_id=None):
    _sheets_service().spreadsheets().values().update(
        spreadsheetId=_ssid(spreadsheet_id),
        range=f"{SHEET_NAME}!{col_letter}{row}",
        valueInputOption="RAW",
        body={"values": [[value]]},
    ).execute()


# ============================================================
# ASIN 自動取得
# ============================================================

_COLOR_MAP = {
    "赤": ["レッド", "Red", "red"],
    "青": ["ブルー", "Blue", "blue"],
    "黒": ["ブラック", "Black", "black"],
    "白": ["ホワイト", "White", "white"],
    "銀": ["シルバー", "Silver", "silver"],
    "シルバー": ["Silver", "silver"],
    "ピンク": ["Pink", "pink"],
    "緑": ["グリーン", "Green", "green"],
    "紫": ["パープル", "Purple", "purple"],
    "金": ["ゴールド", "Gold", "gold"],
}

_INVALID_KW = {"商品名取得失敗", "メルカリで出す", "充電出来ない", "リモコンの方"}


def _build_queries(text: str) -> list[str]:
    if not text or text.strip() in _INVALID_KW or len(text.strip()) < 3:
        return []
    first = text.split("\n")[0].strip()
    queries = [first]
    for ja, alts in _COLOR_MAP.items():
        if ja in first:
            for a in alts:
                queries.append(first.replace(ja, a))
            break
    cleaned = first.replace("本体", "").replace("　", " ").strip()
    if cleaned and cleaned != first:
        queries.append(cleaned)
    return list(dict.fromkeys(queries))


def run_asin_lookup(dry_run: bool = False, spreadsheet_id=None):
    """generator: ASIN 取得ログを yield する。"""
    from sp_api.api import CatalogItems
    from sp_api.base import Marketplaces

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("P", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        asin  = _cell(row, COL_ASIN)
        note  = _cell(row, 20) if len(row) > 20 else ""
        # 仕入れ特記事項: スプシの Q列(index=16) or R列 — 既存スクリプトは df['仕入れ特記事項'] を使用
        # 実際の列は確認済み (asin_lookup.py では load_dataframe で読む)
        # ここでは P列(15)のASINが空で、何か特記事項があれば対象とする
        if asin:
            continue
        # 仕入れ特記事項が入っている列を探す（Q列 = index 16）
        note = _cell(row, 16) if len(row) > 16 else ""
        if not note or note in _INVALID_KW:
            continue
        targets.append((sheet_row, _cell(row, COL_KANRI_ID), note))

    yield f"対象: {len(targets)} 件"
    if not targets:
        yield "対象なし。終了します。"
        return

    api = CatalogItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    success = failed = 0

    for sheet_row, kanri_id, note_raw in targets:
        yield f"[{kanri_id}] 検索: {note_raw[:50]}"
        queries = _build_queries(note_raw)
        found = None
        for q in queries:
            try:
                res = api.search_catalog_items(
                    keywords=q,
                    marketplaceIds=[_marketplace_id()],
                    includedData=["summaries"],
                    pageSize=5,
                )
                items = res.payload.get("items", [])
                if items:
                    found = items[0].get("asin")
                    name  = items[0].get("summaries", [{}])[0].get("itemName", "")
                    yield f"  → ASIN: {found}  {name[:40]}"
                    break
            except Exception as e:
                yield f"  検索エラー: {e}"
            time.sleep(0.5)

        if found:
            if not dry_run:
                _update_cell(sheet_row, "P", found, spreadsheet_id)
                yield f"  → シート書き込み完了"
            else:
                yield f"  → [DRY] 書き込みスキップ"
            success += 1
        else:
            yield f"  → 見つかりませんでした"
            failed += 1

        time.sleep(1)

    yield f"完了: 成功 {success} 件 / 未取得 {failed} 件"


# ============================================================
# 自動出品
# ============================================================

_CONDITION_MAP = {
    "非常に良い": ("used_very_good", COL_NOTE_VG),
    "非常良い":   ("used_very_good", COL_NOTE_VG),
    "非常":       ("used_very_good", COL_NOTE_VG),
    "良い":       ("used_good",      COL_NOTE_G),
    "傷有り。よい": ("used_good",    COL_NOTE_G),
    "許容":       ("used_acceptable", COL_NOTE_G),
    "許容可能":   ("used_acceptable", COL_NOTE_G),
}


def run_auto_listing(dry_run: bool = True, spreadsheet_id=None):
    """generator: 自動出品ログを yield する。"""
    from sp_api.api import ListingsItems
    from sp_api.base import Marketplaces

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("T", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "3.納品済み":
            continue
        asin  = _cell(row, COL_ASIN)
        sku   = _cell(row, COL_SKU)
        price_str = _cell(row, COL_HANBAI)
        state = _cell(row, COL_STATE)
        if not asin or not sku or not price_str:
            continue
        try:
            price = int(float(price_str.replace(",", "").replace("¥", "")))
        except ValueError:
            continue
        ct, note_col = _CONDITION_MAP.get(state, ("used_good", COL_NOTE_G))
        targets.append({
            "sheet_row": sheet_row,
            "kanri_id": _cell(row, COL_KANRI_ID),
            "asin": asin, "sku": sku, "price": price,
            "condition_type": ct,
            "condition_note": _cell(row, note_col),
        })

    yield f"出品対象: {len(targets)} 件"
    if not targets:
        yield "対象なし。終了します。"
        return

    api = None if dry_run else ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    success = failed = 0

    for t in targets:
        yield f"[{t['kanri_id']}] ASIN={t['asin']} | {t['price']}円 | {t['condition_type']}"
        image_urls = _get_image_urls_for_sku(t["sku"])
        yield f"  → 画像: {len(image_urls)}枚{' (Drive フォルダなし)' if not image_urls else ''}"
        if dry_run:
            yield f"  → [DRY] note={t['condition_note'][:40] if t['condition_note'] else '(なし)'}..."
            success += 1
            continue
        try:
            body = {
                "productType": "PRODUCT",
                "requirements": "LISTING",
                "attributes": {
                    "condition_type": [{"value": t["condition_type"]}],
                    "merchant_suggested_asin": [{"value": t["asin"]}],
                    "fulfillment_availability": [{"fulfillment_channel_code": "AMAZON_JP"}],
                    "purchasable_offer": [
                        {"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(t["price"])}]}]}
                    ],
                },
            }
            if t["condition_note"]:
                body["attributes"]["condition_note"] = [{"value": t["condition_note"][:1000]}]
            if image_urls:
                body["attributes"]["main_product_image_locator"] = [
                    {"media_location": image_urls[0]}
                ]
                for i, url in enumerate(image_urls[1:], 1):
                    body["attributes"][f"other_product_image_locator_{i}"] = [
                        {"media_location": url}
                    ]
            resp = api.put_listings_item(
                sellerId=_seller_id(), sku=t["sku"],
                marketplaceIds=[_marketplace_id()], body=body,
            )
            _update_cell(t["sheet_row"], "D", "3.出品済み", spreadsheet_id)
            yield f"  → 出品完了 / ステータス更新"
            success += 1
        except Exception as e:
            yield f"  → エラー: {e}"
            failed += 1
        time.sleep(1)

    yield f"完了: 成功 {success} 件 / 失敗 {failed} 件"


# ============================================================
# 価格自動調整
# ============================================================

MIN_MARGIN_RATE    = 1.15
PRICE_UP_THRESHOLD = 1.20


def _buybox(products_api, asin: str):
    try:
        resp = products_api.get_competitive_pricing_for_asins([asin])
        for prod in (resp.payload or []):
            for cp in prod.get("Product", {}).get("CompetitivePricing", {}).get("CompetitivePrices", []):
                if cp.get("CompetitivePriceId") == "1":
                    amt = cp.get("Price", {}).get("LandedPrice", {}).get("Amount")
                    if amt is not None:
                        return int(float(amt))
    except Exception:
        pass
    return None


def run_auto_reprice(dry_run: bool = True, spreadsheet_id=None):
    """generator: 価格調整ログを yield する。"""
    from sp_api.api import ListingsItems, Products
    from sp_api.base import Marketplaces

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("P", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "3.出品済み":
            continue
        asin  = _cell(row, COL_ASIN)
        sku   = _cell(row, COL_SKU)
        price_str  = _cell(row, COL_HANBAI)
        shiire_str = _cell(row, COL_SHIIRE)
        if not asin or not sku or not price_str:
            continue
        try:
            current = int(float(price_str.replace(",", "").replace("¥", "")))
            shiire  = int(float(shiire_str.replace(",", ""))) if shiire_str else 0
        except ValueError:
            continue
        targets.append({
            "sheet_row": sheet_row,
            "kanri_id": _cell(row, COL_KANRI_ID),
            "asin": asin, "sku": sku,
            "current": current,
            "floor":   math.ceil(shiire * MIN_MARGIN_RATE) if shiire else 1,
            "ceiling": current,
        })

    yield f"調整対象: {len(targets)} 件"
    if not targets:
        yield "対象なし。終了します。"
        return

    products_api = Products(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    listings_api = None if dry_run else ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    updated = skipped = failed = 0

    for t in targets:
        yield f"[{t['kanri_id']}] 現在={t['current']}円 | 下限={t['floor']}円"
        bb = _buybox(products_api, t["asin"])
        time.sleep(2)

        if bb is None:
            yield f"  → BuyBox取得不可: スキップ"
            skipped += 1
            continue

        if bb < t["current"]:
            new_price = max(bb - 1, t["floor"])
            reason = f"アンダーカット (BuyBox={bb}円)"
        elif bb > t["current"] * PRICE_UP_THRESHOLD:
            new_price = max(min(bb - 1, t["ceiling"]), t["floor"])
            reason = f"値上げ (BuyBox={bb}円)"
        else:
            yield f"  → 変更不要 (BuyBox={bb}円)"
            skipped += 1
            continue

        if new_price == t["current"]:
            yield f"  → 変更なし (下限={t['floor']}円 により調整済み)"
            skipped += 1
            continue

        yield f"  → 新価格: {new_price}円 ({reason})"
        if dry_run:
            updated += 1
            continue

        try:
            listings_api.put_listings_item(
                sellerId=_seller_id(), sku=t["sku"],
                marketplaceIds=[_marketplace_id()],
                body={
                    "productType": "PRODUCT",
                    "patches": [{"op": "replace", "path": "/attributes/purchasable_offer",
                                 "value": [{"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(new_price)}]}]}]}],
                },
            )
            _update_cell(t["sheet_row"], "J", str(new_price), spreadsheet_id)
            yield f"  → Amazon + シート更新完了"
            updated += 1
        except Exception as e:
            yield f"  → エラー: {e}"
            failed += 1
        time.sleep(1)

    yield f"完了: 更新 {updated} 件 / スキップ {skipped} 件 / 失敗 {failed} 件"


# ============================================================
# FNSKUラベル PDF 生成
# ============================================================

_CONDITION_JP = {
    "used_very_good": "非常に良い",
    "used_good":      "良い",
    "used_acceptable": "許容可能",
    "new":            "新品",
}


def _get_fnsku(listings_api, sku: str):
    try:
        res = listings_api.get_listings_item(
            sellerId=_seller_id(), sku=sku,
            marketplaceIds=[_marketplace_id()],
            includedData=["summaries"],
        )
        summs = (res.payload or {}).get("summaries", [])
        if summs:
            return summs[0].get("fnSku", ""), summs[0].get("itemName", "")
    except Exception:
        pass
    return "", ""


def _get_image_urls_for_sku(sku: str) -> list[str]:
    """SKU フォルダ内の画像を公開設定にして URL リストを返す（最大6件）。
    Drive ファイルに "anyone reader" permission を付与し、
    Amazon がダウンロードできる直接 URL を構築する。"""
    folder_id = _find_sku_folder(sku)
    if not folder_id:
        return []
    images = _list_images(folder_id, max_count=6)
    svc = _drive_service()
    urls = []
    for img in images:
        try:
            svc.permissions().create(
                fileId=img["id"],
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
        except Exception:
            pass  # すでに公開済み or エラーは無視
        urls.append(f"https://drive.google.com/uc?export=download&id={img['id']}")
    return urls


def _find_sku_folder(sku: str):
    q = (f"'{_drive_folder_id()}' in parents and name='{sku}'"
         " and mimeType='application/vnd.google-apps.folder' and trashed=false")
    res = _drive_service().files().list(q=q, fields="files(id)").execute()
    folders = res.get("files", [])
    return folders[0]["id"] if folders else None


def _list_images(folder_id: str, max_count: int = 6) -> list[dict]:
    q = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false"
    res = _drive_service().files().list(
        q=q, fields="files(id,name)", orderBy="name", pageSize=max_count,
    ).execute()
    return res.get("files", [])[:max_count]


def _download_image(file_id: str) -> io.BytesIO:
    from googleapiclient.http import MediaIoBaseDownload
    req = _drive_service().files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return buf


def _barcode_png(fnsku: str) -> io.BytesIO:
    from barcode import Code128
    from barcode.writer import ImageWriter
    code = Code128(fnsku, writer=ImageWriter())
    buf = io.BytesIO()
    code.write(buf, options={
        "module_height": 15.0, "module_width": 0.55,
        "quiet_zone": 2.5, "font_size": 10,
        "text_distance": 4.0, "write_text": True,
    })
    buf.seek(0)
    return buf


def _draw_page(c, item: dict, today: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.utils import ImageReader
    from PIL import Image as PILImage

    FONT = "HeiseiKakuGo-W5"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    except Exception:
        pass

    W, H = A4
    margin = 18 * mm

    # ヘッダー
    c.setFillColorRGB(0.13, 0.20, 0.50)
    c.rect(0, H - 32 * mm, W, 32 * mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(FONT, 14)
    c.drawString(margin, H - 12 * mm, f"FNSKUラベル   {item['kanri_id']}")
    c.setFont(FONT, 8.5)
    c.drawString(margin,       H - 21 * mm, f"SKU:   {item['sku']}")
    c.drawString(margin + 160, H - 21 * mm, f"ASIN:  {item['asin']}")
    c.drawString(margin + 310, H - 21 * mm, f"FNSKU: {item['fnsku'] or '(未取得)'}")
    c.drawString(margin,       H - 29 * mm,
                 f"コンディション: {item['condition_jp']}   ¥{item['price']:,}   {today}")

    # 商品名
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT, 11)
    name = item["item_name"][:58] + ("…" if len(item["item_name"]) > 58 else "")
    c.drawString(margin, H - 42 * mm, name)

    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.setLineWidth(0.5)
    c.line(margin, H - 46 * mm, W - margin, H - 46 * mm)

    # バーコード
    bc_top = H - 49 * mm
    bc_w, bc_h = 90 * mm, 42 * mm
    if item["fnsku"]:
        try:
            bc_img = ImageReader(_barcode_png(item["fnsku"]))
            c.drawImage(bc_img, margin, bc_top - bc_h, width=bc_w, height=bc_h,
                        preserveAspectRatio=True, anchor="c")
        except Exception:
            pass
    c.setFont(FONT, 11)
    c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(margin + bc_w / 2, bc_top - bc_h - 7 * mm,
                        item["fnsku"] or "(FNSKU未取得)")

    # 画像
    ix = margin + bc_w + 8 * mm
    iw = W - ix - margin
    if item["images"]:
        c.setFont(FONT, 8.5)
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawString(ix, bc_top + 3 * mm, f"商品画像（{len(item['images'])}枚）")
        th_w = (iw - 4 * mm) / 2
        th_h = 32 * mm
        for idx, img_data in enumerate(item["images"][:6]):
            col_i, row_i = idx % 2, idx // 2
            x = ix + col_i * (th_w + 4 * mm)
            y = bc_top - (row_i + 1) * (th_h + 3 * mm)
            try:
                pil = PILImage.open(img_data)
                if pil.mode in ("RGBA", "P", "LA"):
                    pil = pil.convert("RGB")
                out = io.BytesIO()
                pil.save(out, format="JPEG", quality=85)
                out.seek(0)
                c.drawImage(ImageReader(out), x, y, width=th_w, height=th_h,
                            preserveAspectRatio=True, anchor="c")
            except Exception:
                c.setFillColorRGB(0.92, 0.92, 0.92)
                c.rect(x, y, th_w, th_h, fill=1, stroke=0)
    else:
        c.setFont(FONT, 8.5)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        c.drawString(ix, bc_top - 12 * mm, "画像フォルダなし")
        c.setFont(FONT, 7.5)
        c.drawString(ix, bc_top - 19 * mm,
                     f'Drive → フォルダ名「{item["sku"]}」を作成して画像を追加')

    # フッター
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.line(margin, 18 * mm, W - margin, 18 * mm)
    c.setFont(FONT, 7.5)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(margin, 11 * mm, f"センリツ  |  {item['kanri_id']}  |  {item['sku']}")
    c.drawRightString(W - margin, 11 * mm, today)


def run_fnsku_labels(target_sku: str = "", spreadsheet_id=None) -> tuple[bytes, list[str]]:
    """FNSKUラベル PDF を生成して (bytes, log_lines) を返す。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rlcanvas
    from sp_api.api import ListingsItems
    from sp_api.base import Marketplaces
    from datetime import datetime

    logs = []

    def log(msg):
        logs.append(msg)

    log("スプレッドシート読み込み中...")
    all_rows = _read_rows("R", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "3.出品済み":
            continue
        sku  = _cell(row, COL_SKU)
        asin = _cell(row, COL_ASIN)
        if not sku or not asin:
            continue
        if target_sku and sku != target_sku:
            continue
        price_str = _cell(row, COL_HANBAI)
        try:
            price = int(float(price_str.replace(",", "").replace("¥", "")))
        except Exception:
            price = 0
        targets.append({
            "sheet_row": sheet_row,
            "kanri_id":  _cell(row, COL_KANRI_ID),
            "sku": sku, "asin": asin, "price": price,
            "state_raw": _cell(row, COL_STATE),
        })

    log(f"対象: {len(targets)} 件")
    if not targets:
        log("対象なし。")
        return b"", logs

    api = ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    items_for_pdf = []

    for t in targets:
        fnsku, item_name = _get_fnsku(api, t["sku"])
        s = t["state_raw"]
        if "非常" in s:
            ct = "used_very_good"
        elif "良い" in s or "傷" in s:
            ct = "used_good"
        elif "許容" in s:
            ct = "used_acceptable"
        else:
            ct = "used_good"

        images = []
        folder_id = _find_sku_folder(t["sku"])
        if folder_id:
            for f in _list_images(folder_id):
                try:
                    images.append(_download_image(f["id"]))
                except Exception:
                    pass

        log(f"[{t['kanri_id']}] FNSKU={fnsku or '未取得'} 画像={len(images)}枚")
        items_for_pdf.append({
            **t,
            "fnsku": fnsku, "item_name": item_name,
            "condition_type": ct,
            "condition_jp": _CONDITION_JP.get(ct, ct),
            "images": images,
        })
        time.sleep(0.2)

    today = datetime.now().strftime("%Y-%m-%d")
    buf = io.BytesIO()
    c = rlcanvas.Canvas(buf, pagesize=A4)
    for item in items_for_pdf:
        _draw_page(c, item, today)
        c.showPage()
    c.save()

    ok  = sum(1 for it in items_for_pdf if it["fnsku"])
    log(f"PDF 生成完了: {len(items_for_pdf)} ページ")
    log(f"FNSKU 取得: {ok} 件 / 未取得: {len(items_for_pdf) - ok} 件")
    return buf.getvalue(), logs
