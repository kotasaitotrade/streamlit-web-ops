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


def _get_product_type(asin: str) -> str:
    """CatalogItems API で ASIN の productType を取得。失敗時は 'PRODUCT' を返す。"""
    try:
        from sp_api.api import CatalogItems
        from sp_api.base import Marketplaces
        cat = CatalogItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
        resp = cat.get_catalog_item(
            asin=asin,
            marketplaceIds=[_marketplace_id()],
            includedData=["productTypes"],
        )
        pts = resp.payload.get("productTypes", [])
        if pts:
            return pts[0].get("productType", "PRODUCT")
    except Exception:
        pass
    return "PRODUCT"


def _listing_body(asin: str, sku: str, price: int, condition_type: str,
                  condition_note: str = "", image_urls: list = None) -> dict:
    """put_listings_item 用リクエストボディを組み立てる。"""
    product_type = _get_product_type(asin)
    body: dict = {
        "productType": product_type,
        "requirements": "LISTING_OFFER_ONLY",
        "attributes": {
            "condition_type":            [{"value": condition_type}],
            "merchant_suggested_asin":   [{"value": asin}],
            "fulfillment_availability":  [{"fulfillment_channel_code": "AMAZON_JP"}],
            "purchasable_offer": [
                {"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(price)}]}]}
            ],
            "supplier_declared_dg_hz_regulation": [{"value": "not_applicable"}],
        },
    }
    if condition_note:
        body["attributes"]["condition_note"] = [{"value": condition_note[:1000]}]
    if image_urls:
        body["attributes"]["main_product_image_locator"] = [{"media_location": image_urls[0]}]
        for i, url in enumerate(image_urls[1:], 1):
            body["attributes"][f"other_product_image_locator_{i}"] = [{"media_location": url}]
    return body

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

COL_KANRI_ID    = 0
COL_STATUS      = 3
COL_SHIIRE      = 4
COL_HANBAI      = 9
COL_SKU         = 14
COL_ASIN        = 15
COL_STATE       = 17
COL_NOTE_VG     = 18
COL_NOTE_G      = 19
COL_SHIPMENT_ID = 21   # V: FBA Shipment ID (U列は「型番など」で使用中のため)

CONDITION_FBA_MAP = {
    "used_very_good":  "UsedVeryGood",
    "used_good":       "UsedGood",
    "used_acceptable": "UsedAcceptable",
    "new":             "NewItem",
}


def _cell(row: list, col: int) -> str:
    return row[col].strip() if col < len(row) and row[col] else ""


def _read_rows(col_end: str = "T", spreadsheet_id=None) -> list[tuple[int, list]]:
    """(sheet_row, row_data) のリストを返す。sheet_row は 1-indexed。"""
    result = _sheets_service().spreadsheets().values().get(
        spreadsheetId=_ssid(spreadsheet_id),
        range=f"{SHEET_NAME}!A2:{col_end}4000",
    ).execute()
    rows = result.get("values", [])
    return [(i + 2, row) for i, row in enumerate(rows)]


def get_status_counts(spreadsheet_id=None) -> dict:
    """ステータス別件数を返す（サイドバー表示用）。"""
    from collections import Counter
    result = _sheets_service().spreadsheets().values().get(
        spreadsheetId=_ssid(spreadsheet_id),
        range=f"{SHEET_NAME}!D2:D4000",
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
            body = _listing_body(
                asin=t["asin"], sku=t["sku"], price=t["price"],
                condition_type=t["condition_type"],
                condition_note=t["condition_note"],
                image_urls=image_urls,
            )
            resp = api.put_listings_item(
                sellerId=_seller_id(), sku=t["sku"],
                marketplaceIds=[_marketplace_id()], body=body,
            )
            _update_cell(t["sheet_row"], "D", "3.出品済み", spreadsheet_id)
            yield f"  → 出品完了 / ステータス更新"
            moved = _move_sku_folder_to_bk(t["sku"])
            yield f"  → 画像フォルダ: {'BKへ移動済み' if moved else 'フォルダなし（スキップ）'}"
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


def _bk_folder_id() -> str:
    try:
        return st.secrets["amazon_config"]["bk_image_folder_id"]
    except Exception:
        return "1NTaFxO5l1hTOtqBX0PYyFPFZ88Eb6WbC"


def _move_sku_folder_to_bk(sku: str) -> bool:
    """SKU フォルダを BK フォルダへ移動する。成功すれば True。"""
    folder_id = _find_sku_folder(sku)
    if not folder_id:
        return False
    try:
        _drive_service().files().update(
            fileId=folder_id,
            addParents=_bk_folder_id(),
            removeParents=_drive_folder_id(),
            fields="id,parents",
        ).execute()
        return True
    except Exception:
        return False


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
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.utils import ImageReader
    from PIL import Image as PILImage
    import os

    FONT = "NotoSansJP"
    if FONT not in pdfmetrics.getRegisteredFontNames():
        # assets/fonts/ → Streamlit Cloud の /usr/share/fonts/... の順に探す
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "NotoSansJP.ttf"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]
        for path in candidates:
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont(FONT, path))
                break

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
                 f"コンディション: {item['condition_jp']}   {item['price']:,}円   {today}")

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


# ============================================================
# FBA 納品プラン作成
# ============================================================

def _ship_from_address(account_name: str) -> dict:
    """secrets の {account_name}_address から SP-API 用住所 dict を返す。"""
    sec = st.secrets[f"{account_name}_address"]
    return {
        "Name":         sec["name"],
        "AddressLine1": sec["address_line1"],
        "City":         sec["city"],
        "PostalCode":   sec["postal_code"],
        "CountryCode":  sec.get("country_code", "JP"),
    }


def _generate_labels_pdf_from_items(items_for_pdf: list) -> bytes:
    """item dict リストから FNSKU ラベル PDF を生成して bytes を返す。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rlcanvas
    from datetime import datetime
    if not items_for_pdf:
        return b""
    today = datetime.now().strftime("%Y-%m-%d")
    buf = io.BytesIO()
    c = rlcanvas.Canvas(buf, pagesize=A4)
    for item in items_for_pdf:
        _draw_page(c, item, today)
        c.showPage()
    c.save()
    return buf.getvalue()


def _fba_get_access_token() -> str:
    """LWA からアクセストークンを取得"""
    import requests as _req
    c = _sp_creds()
    r = _req.post("https://api.amazon.com/auth/o2/token", data={
        "grant_type": "refresh_token",
        "refresh_token": c["refresh_token"],
        "client_id": c["lwa_app_id"],
        "client_secret": c["lwa_client_secret"],
    }, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


def _fba_wait_op(base_url, headers, op_id, log_fn, timeout=90):
    """FBA v2024 非同期オペレーション完了待ち。成功なら True"""
    import requests as _req, time as _time
    for _ in range(timeout):
        r = _req.get(f"{base_url}/operations/{op_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            st = r.json().get("operationStatus", "")
            if st == "SUCCESS":
                return True
            if st == "FAILED":
                log_fn(f"    オペレーション失敗: {r.json().get('operationProblems', '')}")
                return False
        _time.sleep(1)
    log_fn("    オペレーションタイムアウト")
    return False


def _fba_create_plan_v2024(account_name: str, items: list, log_fn) -> dict | None:
    """FBA Inbound v2024-03-20 でプランを作成（①プラン → ②パッキング → ③配置確定）
    Returns: {"plan_id": str, "placement_option_id": str|None, "shipment_ids": list} or None
    """
    import requests as _req
    from datetime import datetime as _dt

    BASE = "https://sellingpartnerapi-fe.amazon.com/inbound/fba/2024-03-20"

    try:
        access_token = _fba_get_access_token()
    except Exception as e:
        log_fn(f"  → トークン取得エラー: {e}")
        return None

    headers = {
        "x-amz-access-token": access_token,
        "Content-Type": "application/json",
    }

    # 発送元住所（Streamlit Secrets から: {account_name}_address セクション）
    try:
        addr = dict(st.secrets.get(f"{account_name}_address", {}))
    except Exception:
        addr = {}

    if not addr.get("name") or not addr.get("phone"):
        log_fn(f"  → 発送元住所が未設定。Secrets の [{account_name}_address] に name / phone を追加してください")
        return None

    source_address = {
        "name":                 addr["name"],
        "phoneNumber":          addr["phone"],
        "addressLine1":         addr.get("address_line1", ""),
        "city":                 addr.get("city", ""),
        "postalCode":           addr.get("postal_code", ""),
        "stateOrProvinceCode":  addr.get("state", "Tokyo"),
        "countryCode":          "JP",
    }
    if addr.get("address_line2"):
        source_address["addressLine2"] = addr["address_line2"]

    # 事前チェック: prepCategory が UNKNOWN の SKU は Seller Central で設定が必要
    skus = [it["sku"] for it in items]
    r_prep = _req.get(f"{BASE}/items/prepDetails", headers=headers,
        params={"mskus": ",".join(skus), "marketplaceId": _marketplace_id()}, timeout=15)
    prep_map = {}
    if r_prep.status_code == 200:
        for d in r_prep.json().get("mskuPrepDetails", []):
            prep_map[d["msku"]] = d
    unknown_skus = [m for m, d in prep_map.items() if d.get("prepCategory", "UNKNOWN") == "UNKNOWN"]
    if unknown_skus:
        log_fn(f"  ⚠️ 以下の SKU はプレップカテゴリが未設定です: {', '.join(unknown_skus)}")
        log_fn("  → Seller Central で一度手動で FBA 納品プランを作成しプレップカテゴリを設定してください。")
        log_fn("     https://sellercentral.amazon.co.jp/fba/inbound/index.html")
        return None

    # prepOwner は "NONE"（API が SELLER/AMAZON を拒否する）
    plan_items = [
        {"labelOwner": "SELLER", "msku": it["sku"], "prepOwner": "NONE", "quantity": 1}
        for it in items
    ]

    # ①プラン作成
    plan_name = f"auto-{account_name}-{_dt.now().strftime('%Y%m%d-%H%M')}"
    log_fn(f"  ①プラン作成中: {len(plan_items)} 件 …")
    r = _req.post(f"{BASE}/inboundPlans", headers=headers, json={
        "destinationMarketplaces": [_marketplace_id()],
        "items": plan_items,
        "name": plan_name,
        "sourceAddress": source_address,
    }, timeout=30)

    if r.status_code != 202:
        errs = r.json().get("errors", [{}])
        msg = errs[0].get("message", r.text[:200]) if errs else r.text[:200]
        log_fn(f"  → プラン作成エラー: {r.status_code} {msg}")
        return None

    data = r.json()
    plan_id = data["inboundPlanId"]
    log_fn(f"  → プランID: {plan_id}")
    if not _fba_wait_op(BASE, headers, data["operationId"], log_fn):
        return None

    # ②パッキングオプション生成 → 確定
    log_fn("  ②パッキングオプション生成中 …")
    r2 = _req.post(f"{BASE}/inboundPlans/{plan_id}/packingOptions", headers=headers, json={}, timeout=15)
    if r2.status_code == 202:
        _fba_wait_op(BASE, headers, r2.json()["operationId"], log_fn)

    r3 = _req.get(f"{BASE}/inboundPlans/{plan_id}/packingOptions", headers=headers, timeout=15)
    pack_opts = r3.json().get("packingOptions", []) if r3.status_code == 200 else []
    if pack_opts:
        opt_id = pack_opts[0]["packingOptionId"]
        r4 = _req.post(f"{BASE}/inboundPlans/{plan_id}/packingOptions/{opt_id}/confirmation",
                       headers=headers, json={}, timeout=15)
        if r4.status_code == 202:
            _fba_wait_op(BASE, headers, r4.json()["operationId"], log_fn)
        log_fn(f"  → パッキング確定")

    # ③配置オプション生成 → 手数料最安を確定（fees は list 形式）
    log_fn("  ③配置オプション生成中 …")
    r5 = _req.post(f"{BASE}/inboundPlans/{plan_id}/placementOptions", headers=headers, json={}, timeout=15)
    if r5.status_code == 202:
        _fba_wait_op(BASE, headers, r5.json()["operationId"], log_fn, timeout=120)

    r6 = _req.get(f"{BASE}/inboundPlans/{plan_id}/placementOptions", headers=headers, timeout=15)
    placements = r6.json().get("placementOptions", []) if r6.status_code == 200 else []
    ship_ids = []
    p_id = None
    if placements:
        best = min(
            placements,
            key=lambda x: sum(f.get("value", {}).get("amount", 0) for f in x.get("fees", [])),
        )
        p_id = best["placementOptionId"]
        ship_ids = best.get("shipmentIds", [])
        fee_total = sum(f.get("value", {}).get("amount", 0) for f in best.get("fees", []))
        r7 = _req.post(f"{BASE}/inboundPlans/{plan_id}/placementOptions/{p_id}/confirmation",
                       headers=headers, json={}, timeout=15)
        if r7.status_code == 202:
            _fba_wait_op(BASE, headers, r7.json()["operationId"], log_fn)
        log_fn(f"  → 配置確定  手数料: JPY {fee_total:,}")

    # ④発送先 FC 住所と出荷確認ID を表示
    for ship_id in ship_ids:
        r_ship = _req.get(f"{BASE}/inboundPlans/{plan_id}/shipments/{ship_id}", headers=headers, timeout=15)
        if r_ship.status_code == 200:
            sd = r_ship.json()
            dest = sd.get("destination", {}).get("address", {})
            confirm_id = sd.get("shipmentConfirmationId", "")
            if dest:
                log_fn(f"  📦 発送先FC: {dest.get('name','')}  {dest.get('addressLine1','')} {dest.get('city','')} {dest.get('postalCode','')}")
            if confirm_id:
                log_fn(f"  📋 出荷確認ID: {confirm_id}")

    log_fn("  ✅ プラン作成完了！（配置確定まで完了）")

    return {"plan_id": plan_id, "placement_option_id": p_id, "shipment_ids": ship_ids}


def run_fba_inbound(account_name: str, dry_run: bool = True, spreadsheet_id=None):
    """2.写真撮影済み → 出品登録(FNSKU) → FNSKUラベルPDF → FBA納品プラン → 3.発送待ち
    戻り値: (log_lines, fnsku_pdf_bytes, shipment_summary_list)
    """
    from sp_api.api import ListingsItems
    from sp_api.base import Marketplaces
    from datetime import datetime

    logs = []
    def log(msg): logs.append(msg)

    log("スプレッドシート読み込み中...")
    all_rows = _read_rows("T", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "2.写真撮影済み":
            continue
        asin      = _cell(row, COL_ASIN)
        sku       = _cell(row, COL_SKU)
        price_str = _cell(row, COL_HANBAI)
        state     = _cell(row, COL_STATE)
        if not asin or not sku or not price_str:
            continue
        try:
            price = int(float(price_str.replace(",", "").replace("¥", "")))
        except ValueError:
            continue
        ct, note_col = _CONDITION_MAP.get(state, ("used_good", COL_NOTE_G))
        targets.append({
            "sheet_row":     sheet_row,
            "kanri_id":      _cell(row, COL_KANRI_ID),
            "asin":          asin,
            "sku":           sku,
            "price":         price,
            "condition_type": ct,
            "condition_note": _cell(row, note_col),
            "condition_fba":  CONDITION_FBA_MAP.get(ct, "UsedGood"),
            "state_raw":     state,
        })

    log(f"対象: {len(targets)} 件（2.写真撮影済み）")
    if not targets:
        log("対象なし。終了します。")
        return logs, b"", []

    # ─── ① 出品登録（FNSKU取得）──────────────────────────────
    log("=== ① 出品登録（FNSKU取得）===")
    listings_api = ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    items_for_pdf = []

    for t in targets:
        log(f"[{t['kanri_id']}] {t['sku']} 出品登録中...")
        fnsku = ""
        item_name = ""
        if not dry_run:
            try:
                image_urls = _get_image_urls_for_sku(t["sku"])
                body = _listing_body(
                    asin=t["asin"], sku=t["sku"], price=t["price"],
                    condition_type=t["condition_type"],
                    condition_note=t["condition_note"],
                    image_urls=image_urls,
                )
                listings_api.put_listings_item(
                    sellerId=_seller_id(), sku=t["sku"],
                    marketplaceIds=[_marketplace_id()], body=body,
                )
                time.sleep(1)
                fnsku, item_name = _get_fnsku(listings_api, t["sku"])
                if not fnsku:
                    log(f"  → FNSKU未取得。3秒後にリトライ...")
                    time.sleep(3)
                    fnsku, item_name = _get_fnsku(listings_api, t["sku"])
                log(f"  → FNSKU: {fnsku or '未取得'}")
            except Exception as e:
                log(f"  → エラー: {e}")
        else:
            log(f"  → [DRY] ASIN={t['asin']} | {t['price']}円 | {t['condition_type']}")
            fnsku = "X00000DRY"

        # Drive 画像取得（PDF用）
        images = []
        try:
            folder_id = _find_sku_folder(t["sku"])
            if folder_id:
                for f in _list_images(folder_id):
                    try:
                        images.append(_download_image(f["id"]))
                    except Exception:
                        pass
        except Exception:
            pass

        ct = t["condition_type"]
        items_for_pdf.append({
            "kanri_id":     t["kanri_id"],
            "sku":          t["sku"],
            "asin":         t["asin"],
            "price":        t["price"],
            "fnsku":        fnsku,
            "item_name":    item_name,
            "condition_type": ct,
            "condition_jp": _CONDITION_JP.get(ct, ct),
            "images":       images,
        })
        log(f"  → 画像: {len(images)}枚")
        time.sleep(0.5)

    # ─── ② FNSKUラベル PDF 生成 ──────────────────────────────
    log("=== ② FNSKUラベル PDF 生成 ===")
    fnsku_pdf = _generate_labels_pdf_from_items(items_for_pdf)
    ok = sum(1 for it in items_for_pdf if it["fnsku"] and it["fnsku"] != "X00000DRY")
    log(f"  → {len(items_for_pdf)} ページ生成 (FNSKU取得: {ok}件)")

    # ─── ③ FBA 納品プラン作成（v2024-03-20）────────────────────
    log("=== ③ FBA 納品プラン作成 ===")
    plan_result_obj = None
    if not dry_run:
        fba_items = [it for it in items_for_pdf if it.get("fnsku") and it["fnsku"] != "X00000DRY"]
        if fba_items:
            plan_result_obj = _fba_create_plan_v2024(account_name, fba_items, log)
        else:
            log("  → FNSKU 取得済みアイテムなし。スキップ。")

        plan_id = plan_result_obj["plan_id"] if plan_result_obj else None

        # ステータス更新（FBA プラン成否に関わらず FNSKU 取得済みは発送待ちへ）
        updated = 0
        for it in items_for_pdf:
            if not it.get("fnsku") or it["fnsku"] == "X00000DRY":
                continue
            sku = it["sku"]
            matched = [t for t in targets if t["sku"] == sku]
            for t in matched:
                _update_cell(t["sheet_row"], "D", "3.発送待ち", spreadsheet_id)
                log(f"  [{t['kanri_id']}] → 3.発送待ち")
                updated += 1
        log(f"  → {updated} 件更新完了")

        if plan_id:
            log(f"  ✅ FBA 納品プラン作成完了: {plan_id}")
        else:
            log("  ⚠️ FBA プランの自動作成に失敗。Seller Central から手動で作成してください。")
            log("    https://sellercentral.amazon.co.jp/fba/sendtoamazon")
    else:
        log(f"  → [DRY] {len(targets)} 件 → 3.発送待ち（スキップ）")

    log("=== 完了 ===")
    return logs, fnsku_pdf, plan_result_obj or {}


# ============================================================
# FBA 受取確認
# ============================================================
# FBA 輸送方法（Transportation Options）
# ============================================================

def get_fba_transportation_options(
    account_name: str,
    plan_id: str,
    placement_option_id: str,
    shipment_ids: list,
    boxes: list,
) -> dict:
    """輸送オプションを生成・取得する。
    boxes: [{"length_cm": float, "width_cm": float, "height_cm": float, "weight_kg": float}]
    Returns: {"options": [...], "error": None} or {"options": [], "error": "message"}
    """
    import requests as _req

    BASE = "https://sellingpartnerapi-fe.amazon.com/inbound/fba/2024-03-20"

    try:
        access_token = _fba_get_access_token()
    except Exception as e:
        return {"options": [], "error": f"トークン取得エラー: {e}"}

    headers = {
        "x-amz-access-token": access_token,
        "Content-Type": "application/json",
    }

    try:
        addr = dict(st.secrets.get(f"{account_name}_address", {}))
        email = addr.get("email", "")
    except Exception:
        email = ""

    cartons = [
        {
            "dimensions": {
                "height": b["height_cm"],
                "length": b["length_cm"],
                "width": b["width_cm"],
                "unitOfMeasurement": "CM",
            },
            "weight": {"value": b["weight_kg"], "unit": "KG"},
        }
        for b in boxes
    ]

    configurations = []
    for ship_id in shipment_ids:
        cfg = {
            "shipmentId": ship_id,
            "shippingConfiguration": {
                "shipmentType": "SP",
                "cartons": cartons,
            },
        }
        if email:
            cfg["contactInformation"] = {"email": email}
        configurations.append(cfg)

    body = {
        "placementOptionId": placement_option_id,
        "shipmentTransportationConfigurations": configurations,
    }

    r = _req.post(
        f"{BASE}/inboundPlans/{plan_id}/transportationOptions",
        headers=headers, json=body, timeout=30,
    )

    if r.status_code == 403:
        return {
            "options": [],
            "error": (
                "403 Forbidden: inbound_shipment_transport_write スコープが付与されていません。\n"
                "SP-API デベロッパーコンソールでスコープ申請が必要です。"
            ),
        }

    if r.status_code != 202:
        errs = r.json().get("errors", [{}])
        msg = errs[0].get("message", r.text[:300]) if errs else r.text[:300]
        return {"options": [], "error": f"{r.status_code}: {msg}"}

    op_id = r.json().get("operationId", "")
    if op_id:
        _fba_wait_op(BASE, headers, op_id, lambda _: None, timeout=60)

    r2 = _req.get(
        f"{BASE}/inboundPlans/{plan_id}/transportationOptions",
        headers=headers, timeout=15,
    )
    if r2.status_code != 200:
        return {"options": [], "error": f"一覧取得エラー: {r2.status_code} {r2.text[:200]}"}

    return {"options": r2.json().get("transportationOptions", []), "error": None}


def confirm_fba_transportation(
    account_name: str,
    plan_id: str,
    selections: list,
) -> dict:
    """輸送方法を確定する。
    selections: [{"shipment_id": str, "transportation_option_id": str}]
    Returns: {"success": bool, "error": None or str}
    """
    import requests as _req

    BASE = "https://sellingpartnerapi-fe.amazon.com/inbound/fba/2024-03-20"

    try:
        access_token = _fba_get_access_token()
    except Exception as e:
        return {"success": False, "error": f"トークン取得エラー: {e}"}

    headers = {
        "x-amz-access-token": access_token,
        "Content-Type": "application/json",
    }

    try:
        addr = dict(st.secrets.get(f"{account_name}_address", {}))
        email = addr.get("email", "")
    except Exception:
        email = ""

    sel_list = []
    for s in selections:
        item = {
            "shipmentId": s["shipment_id"],
            "selectedTransportationOptionId": s["transportation_option_id"],
        }
        if email:
            item["contactInformation"] = {"email": email}
        sel_list.append(item)

    r = _req.post(
        f"{BASE}/inboundPlans/{plan_id}/transportationOptions/confirmation",
        headers=headers,
        json={"shipmentTransportationSelections": sel_list},
        timeout=30,
    )

    if r.status_code == 403:
        return {"success": False, "error": "403 Forbidden: スコープが付与されていません"}

    if r.status_code != 202:
        errs = r.json().get("errors", [{}])
        msg = errs[0].get("message", r.text[:300]) if errs else r.text[:300]
        return {"success": False, "error": f"{r.status_code}: {msg}"}

    op_id = r.json().get("operationId", "")
    if op_id:
        _fba_wait_op(BASE, headers, op_id, lambda _: None, timeout=60)

    return {"success": True, "error": None}


# ============================================================

def run_receipt_check(spreadsheet_id=None):
    """generator: 3.発送待ち → Amazon 受取確認 → 3.出品済み に更新。"""
    from sp_api.api import FulfillmentInboundV0
    from sp_api.base import Marketplaces

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("V", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "3.発送待ち":
            continue
        shipment_id = _cell(row, COL_SHIPMENT_ID)
        if not shipment_id:
            continue
        targets.append({
            "sheet_row":   sheet_row,
            "kanri_id":    _cell(row, COL_KANRI_ID),
            "sku":         _cell(row, COL_SKU),
            "shipment_id": shipment_id,
        })

    yield f"確認対象: {len(targets)} 件（3.発送待ち）"
    if not targets:
        yield "確認対象なし。終了します。"
        return

    fba_api = FulfillmentInboundV0(credentials=_sp_creds(), marketplace=Marketplaces.JP)

    shipments: dict = {}
    for t in targets:
        shipments.setdefault(t["shipment_id"], []).append(t)

    updated = 0
    for shipment_id, items in shipments.items():
        yield f"[{shipment_id}] 確認中... ({len(items)}件)"
        try:
            resp = fba_api.get_shipments(
                QueryType="SHIPMENT",
                MarketplaceId=_marketplace_id(),
                ShipmentIdList=[shipment_id],
            )
            data = resp.payload.get("ShipmentData", [])
            if not data:
                yield "  → データなし（まだ Amazon に届いていない可能性があります）"
                continue
            status = data[0].get("ShipmentStatus", "UNKNOWN")
            yield f"  → ステータス: {status}"
            if status in ("RECEIVING", "CLOSED", "CHECKED_IN"):
                for t in items:
                    _update_cell(t["sheet_row"], "D", "3.出品済み", spreadsheet_id)
                    yield f"  → [{t['kanri_id']}] 3.出品済みに更新"
                    updated += 1
            else:
                yield f"  → 受取前（{status}）— 発送後しばらく待ってから再確認してください"
        except Exception as e:
            yield f"  → エラー: {e}"

    yield f"完了: {updated} 件を 3.出品済み に更新しました"
