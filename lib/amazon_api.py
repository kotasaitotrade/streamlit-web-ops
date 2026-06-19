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

# 「商品管理」シートの列インデックス（0始まり） ← スプレッドシートの列順変更禁止
# A  B   C       D         E        F       G       H          I       J
# 0  1   2       3         4        5       6       7          8       9
# 管ID 画像 出品サイト ステータス 仕入れ日 仕入れ値 仕入れ元 問合番号 仕入れ担当 販売価格
#
# K   L         M    N    O    P     Q        R        S               T
# 10  11        12   13   14   15    16       17       18              19
# 不良 仕入れ特記 管理者 撮影日 SKU  ASIN 状態-仕入れ 状態-撮影 商品情報-非常に良い 商品情報-良い
#
# U     V   W    X   Y    Z     AA    AB  AC              AD         AE
# 20    21  22   23  24   25    26    27  28              29         30
# 型番など 状態 状態2 備考 計算用 問合発番日 問合発番日 (空) カート価格予想 FBAライバル数 最低販売価格
COL_KANRI_ID    = 0
COL_STATUS      = 3
COL_SHIIRE      = 4
COL_HANBAI      = 9
COL_SKU         = 14
COL_ASIN        = 15
COL_STATE       = 17  # R: 状態-撮影（非常に良い/良い/許容可能）
COL_NOTE_VG     = 18  # S: 商品情報入力-非常に良い
COL_NOTE_G      = 19  # T: 商品情報入力-良い
COL_SHIPMENT_ID = 21  # V: 状態（FBA Shipment ID 用途で使用）
COL_CART_PRICE  = 28  # AC: カートに入る価格予想（自動書き込み）
COL_RIVAL_COUNT = 29  # AD: 同条件FBAライバル数（自動書き込み）
COL_MIN_PRICE   = 30  # AE: 最低販売価格（空=現在価格を下限として扱う）

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

# L列(仕入れ特記事項)が「商品名」ではなく作業依頼メモのときに含まれる語。
# これが含まれる場合は L列で検索せず U列(型番など)を検索に使う。
_REQUEST_KW = ("お願い", "おねがい", "写真", "メモ", "年製", "教えて", "確認して")


def _is_request_note(text: str) -> bool:
    """『年製がわかる写真かメモをお願いします』等、商品名でない依頼メモかを判定。"""
    return bool(text) and any(kw in text for kw in _REQUEST_KW)


# 型番が一致しても「アクセサリ・互換品」を本体と誤採用しないための除外語。
# 例: 'HAC-001 対応 バッテリー' は型番HAC-001を含むが本体ではない。
_ACCESSORY_KW = (
    "対応", "互換", "交換用", "ケース", "カバー", "フィルム", "保護", "バッテリー",
    "充電", "ケーブル", "ストラップ", "スタンド", "ガラス", "ホルダー", "グリップ",
    "シール", "アダプタ", "コネクタ", "収納", "ラバー",
    # バンドル/同梱版（中古再販は単体が大半。同梱版は別物として除外）
    "バリューパック", "バリュー・パック", "同梱", "ギフトパック", "ギフトセット",
)


def _is_accessory_title(name: str) -> bool:
    """商品名がアクセサリ・互換品・同梱版らしいか（本体単体でない可能性が高いか）を判定。"""
    return bool(name) and any(kw in name for kw in _ACCESSORY_KW)


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
    # 型番フォールバック: 型番だけのクエリを末尾に追加（色付き等で0件のとき用）
    for m in _extract_models(first):
        if m not in queries:
            queries.append(m)
    return list(dict.fromkeys(queries))


def _extract_models(text: str) -> list[str]:
    """テキストから型番らしいトークン（英字と数字が混在、長さ3以上）を抽出する。
    例: 'PSP-3000 ラディアンレッド' → ['PSP-3000'],
        'SEIKO ソーラー腕時計/デジアナ/H851-00B0' → ['H851-00B0']"""
    import re
    models = []
    for tok in re.split(r"[\s/／,，、　|｜]+", text):
        tok = tok.strip("　 .。・")
        if len(tok) >= 3 and re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok):
            models.append(tok)
    return models


def _normalize_model(s: str) -> str:
    """型番比較用の正規化: 大文字化し、空白・ハイフン・中黒・ドットを除去する。"""
    import re
    return re.sub(r"[\s\-‐－―・･.　]+", "", s or "").upper()


def run_asin_lookup(dry_run: bool = False, spreadsheet_id=None):
    """generator: ASIN 取得ログを yield する。"""
    from sp_api.api import CatalogItems
    from sp_api.base import Marketplaces

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("U", spreadsheet_id)

    # 検索クエリ元の列（実シート確認済み）:
    #   L列(index 11) = 仕入れ特記事項（型番・商品名）← 主。
    #   U列(index 20) = 型番など ← L列が空のときのフォールバック。
    # （Q列 index 16 は「状態-仕入れ」=コンディションであり検索クエリには使わない）
    COL_SHIIRE_NOTE = 11
    COL_MODEL_NOTE  = 20

    targets = []
    for sheet_row, row in all_rows:
        asin = _cell(row, COL_ASIN)
        if asin:
            continue
        # ASIN が空の行のみ対象。
        l_note = _cell(row, COL_SHIIRE_NOTE)   # L列: 仕入れ特記事項（商品名・型番）
        u_note = _cell(row, COL_MODEL_NOTE)    # U列: 型番など
        # L列が「依頼メモ」（例: 年製がわかる写真かメモをお願いします）なら U列を検索に使う
        if l_note and not _is_request_note(l_note):
            search_note = l_note
        elif u_note:
            search_note = u_note
        else:
            search_note = l_note
        if not search_note or search_note in _INVALID_KW:
            continue
        # 型番抽出は L+U 両方から（依頼メモでも U列の型番を拾えるように）
        model_src = f"{l_note} {u_note}".strip()
        targets.append((sheet_row, _cell(row, COL_KANRI_ID), search_note, model_src))

    yield f"対象: {len(targets)} 件"
    if not targets:
        yield "対象なし。終了します。"
        return

    api = CatalogItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    written = rejected = notfound = 0

    for sheet_row, kanri_id, note_raw, model_src in targets:
        yield f"[{kanri_id}] 検索: {note_raw[:50]}"
        queries = _build_queries(note_raw)
        # 型番は検索クエリ＋型番ソース(L+U)の両方から抽出
        models = list(dict.fromkeys(_extract_models(note_raw) + _extract_models(model_src)))
        norm_models = [_normalize_model(m) for m in models]

        match_asin = match_name = None   # 型番一致した候補
        cand_asin = cand_name = None     # 一致しなかったが最初に返ってきた候補

        for q in queries:
            try:
                res = api.search_catalog_items(
                    keywords=q,
                    marketplaceIds=[_marketplace_id()],
                    includedData=["summaries"],
                    pageSize=5,
                )
                items = res.payload.get("items", [])
            except Exception as e:
                yield f"  検索エラー: {e}"
                time.sleep(0.5)
                continue

            if not items:
                time.sleep(0.5)
                continue

            # 最初に何か返ってきたものを「候補」として保持（型番なしの場合の参考用）
            if cand_asin is None:
                cand_asin = items[0].get("asin")
                cand_name = items[0].get("summaries", [{}])[0].get("itemName", "")

            # 型番一致を優先して探す（返ってきた商品名に型番が含まれ、かつアクセサリでない）
            if norm_models:
                for it in items:
                    nm = it.get("summaries", [{}])[0].get("itemName", "")
                    nm_norm = _normalize_model(nm)
                    if any(m in nm_norm for m in norm_models) and not _is_accessory_title(nm):
                        match_asin = it.get("asin")
                        match_name = nm
                        break
            if match_asin:
                break
            time.sleep(0.5)

        # ── 採用判定（型番一致のみ採用。含まれなければ不採用） ──
        if match_asin:
            label = "✅ 型番一致"
            yield f"  {label}: {match_asin}  {match_name[:40]}"
            if not dry_run:
                _update_cell(sheet_row, "P", match_asin, spreadsheet_id)
                yield "  → シート書き込み完了"
            else:
                yield "  → [DRY] 書き込みスキップ"
            written += 1
        elif cand_asin:
            # 候補はあるが型番一致しない → 不採用（人間が選別できるよう候補を表示）
            reason = "型番不一致" if models else "型番なしで検証不可"
            yield f"  ⚠️ {reason}のため不採用（候補: {cand_asin} {(cand_name or '')[:35]}）"
            rejected += 1
        else:
            yield "  → 見つかりませんでした"
            notfound += 1

        time.sleep(1)

    yield f"完了: 書き込み {written} 件 / 不採用 {rejected} 件 / 未ヒット {notfound} 件"


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



# ============================================================
# 価格自動調整
# ============================================================

# 商品コンディション → SP-API SubCondition 値のマッピング
_SUBCONDITION_MAP = {
    "used_very_good":  "very_good",
    "used_good":       "good",
    "used_acceptable": "acceptable",
    "new":             "new",
}


def _get_item_offers(products_api, asin: str) -> dict | None:
    """get_item_offers で ASIN の中古出品一覧を取得。失敗時は None。"""
    try:
        resp = products_api.get_item_offers(asin=asin, item_condition="Used")
        return resp.payload
    except Exception:
        return None


def _analyze_fba_offers(payload: dict | None, sub_condition: str) -> tuple[int | None, int]:
    """同じ SubCondition かつ FBA(Prime) 出品者を分析して (最安値, FBA数) を返す。
    FBA 出品者がいない場合は BuyBox 価格を最安値として返し、FBA数は 0。"""
    if not payload:
        return None, 0
    min_price = None
    count = 0
    for offer in payload.get("Offers", []):
        if offer.get("SubCondition", "").lower() != sub_condition.lower():
            continue
        if not offer.get("PrimeInformation", {}).get("IsPrime"):
            continue
        count += 1
        try:
            price = int(float(offer["ListingPrice"]["Amount"]))
        except Exception:
            continue
        if min_price is None or price < min_price:
            min_price = price
    if min_price is not None:
        return min_price, count
    # FBA競合なし → BuyBox価格にフォールバック（countは0のまま）
    for bb in payload.get("Summary", {}).get("BuyBoxPrices", []):
        try:
            price = int(float(bb["LandedPrice"]["Amount"]))
            if min_price is None or price < min_price:
                min_price = price
        except Exception:
            pass
    return min_price, 0


def _extract_offer_details(payload: dict | None, sub_condition: str) -> tuple:
    """get_listings_offer のペイロードから詳細情報を抽出する。
    Returns: (cart_price, cart_cond, lowest_price, lowest_cond, fba_rival_count, fba_min_price, is_winning)
      - cart_price/cart_cond: 現在のカート獲得者の価格と状態
      - lowest_price/lowest_cond: 全出品者の中で最安値の価格と状態
      - fba_rival_count: 同コンディション・FBA の競合数
      - fba_min_price: 同コンディション FBA の最安値（カート価格予想算出用）
      - is_winning: 自分がカートを取っているか（MyOffer=true かつ IsBuyBoxWinner=true）
    """
    _SUBCOND_JA = {
        "new": "新品", "mint": "ほぼ新品",
        "very_good": "非常に良い", "good": "良い", "acceptable": "可",
    }

    if not payload:
        return None, "", None, "", 0, None, False, False, False

    cart_price = cart_cond = None
    lowest_price = lowest_cond = None
    fba_rival_count = 0
    fba_min_price = None
    is_winning = False
    my_offer_exists = False
    any_bb_winner = False  # 誰かがIsBuyBoxWinner=Trueを持つか
    my_fba_price_in_cond = None  # 自社のFBA価格（同コンディション内）

    for offer in payload.get("Offers", []):
        try:
            price = int(float(offer["ListingPrice"]["Amount"]))
        except Exception:
            continue

        sub      = offer.get("SubCondition", "").lower()
        is_fba   = offer.get("IsFulfilledByAmazon", False)
        is_prime = offer.get("PrimeInformation", {}).get("IsPrime", False)
        is_bb    = offer.get("IsBuyBoxWinner", False)
        my_offer = offer.get("MyOffer", False)
        cond_ja  = _SUBCOND_JA.get(sub, sub or "不明")
        channel  = "Amazon" if is_fba else "出品者"
        label    = f"{cond_ja}({channel})"

        if is_bb:
            cart_price = price
            cart_cond  = label
            any_bb_winner = True

        if my_offer:
            my_offer_exists = True
            if is_bb:
                is_winning = True
            if is_fba and sub == sub_condition.lower():
                my_fba_price_in_cond = price

        if lowest_price is None or price < lowest_price:
            lowest_price = price
            lowest_cond  = label

        if (is_prime or is_fba) and sub == sub_condition.lower():
            fba_rival_count += 1
            if fba_min_price is None or price < fba_min_price:
                fba_min_price = price

    # 新品がメインカートを取っているか（BuyBoxPrices に cond=New が含まれる）
    buy_box_prices = payload.get("Summary", {}).get("BuyBoxPrices", [])
    has_new_buybox = any(
        bb.get("condition", "").lower() in ("new", "新品")
        for bb in buy_box_prices
    )

    # フォールバック判定: BuyBoxPricesが空かつ誰もIsBuyBoxWinner=Trueでない場合、
    # 自社FBAが同コンディション最安値なら実質カート獲得とみなす
    if (not is_winning and my_offer_exists and not any_bb_winner
            and not buy_box_prices and my_fba_price_in_cond is not None
            and my_fba_price_in_cond == fba_min_price):
        is_winning = True

    # フォールバック: BuyBoxPrices から cart_price を補完（Used限定）
    if cart_price is None:
        for bb in buy_box_prices:
            if bb.get("condition", "").lower() not in ("new", "新品"):
                try:
                    cart_price = int(float(bb["LandedPrice"]["Amount"]))
                    cart_cond  = _SUBCOND_JA.get(bb.get("condition", "").lower(), "") + "(Amazon)"
                    break
                except Exception:
                    pass

    # フォールバック: fba_min_price (カート価格予想用)
    if fba_min_price is None:
        for bb in buy_box_prices:
            if bb.get("condition", "").lower() not in ("new", "新品"):
                try:
                    p = int(float(bb["LandedPrice"]["Amount"]))
                    if fba_min_price is None or p < fba_min_price:
                        fba_min_price = p
                except Exception:
                    pass

    return cart_price, cart_cond or "", lowest_price, lowest_cond or "", fba_rival_count, fba_min_price, is_winning, has_new_buybox, my_offer_exists


def _analyze_offers_smart(payload: dict | None, sub_condition: str) -> dict:
    """カート連動リプライス用の解析。同コンディションのオファーを FBA/FBM 別に分析する。
    競合は自社オファー(MyOffer)を除外して集計する。

    Returns dict:
      my_price        : 自社の同コンディション出品価格（無ければ None）
      is_winning      : 自分がカート(BuyBox)を取得しているか
      cart_price      : 現在のカート価格
      cart_is_fba     : カート獲得者が FBA か（True/False/None）
      min_fba_comp    : 同コンディション FBA 競合の最安値（自社除く）
      min_fbm_comp    : 同コンディション FBM 競合の最安値（自社除く）
      fba_rival_count : 同コンディション FBA 競合数（自社除く）
      fbm_rival_count : 同コンディション FBM 競合数（自社除く）
    """
    res = {
        "my_price": None, "is_winning": False, "cart_price": None, "cart_is_fba": None,
        "min_fba_comp": None, "min_fbm_comp": None, "fba_rival_count": 0, "fbm_rival_count": 0,
    }
    if not payload:
        return res
    sc = sub_condition.lower()
    for offer in payload.get("Offers", []):
        try:
            price = int(float(offer["ListingPrice"]["Amount"]))
        except Exception:
            continue
        sub    = offer.get("SubCondition", "").lower()
        is_fba = bool(offer.get("IsFulfilledByAmazon", False)
                      or offer.get("PrimeInformation", {}).get("IsPrime", False))
        is_bb  = offer.get("IsBuyBoxWinner", False)
        mine   = offer.get("MyOffer", False)

        if is_bb:
            res["cart_price"] = price
            res["cart_is_fba"] = is_fba

        if mine:
            if sub == sc:
                res["my_price"] = price
            if is_bb:
                res["is_winning"] = True
            continue  # 自社は競合集計から除外

        if sub != sc:
            continue
        if is_fba:
            res["fba_rival_count"] += 1
            if res["min_fba_comp"] is None or price < res["min_fba_comp"]:
                res["min_fba_comp"] = price
        else:
            res["fbm_rival_count"] += 1
            if res["min_fbm_comp"] is None or price < res["min_fbm_comp"]:
                res["min_fbm_comp"] = price
    return res


def _decide_buybox_target(a: dict, current: int, step: int, raise_step: int,
                          fbm_premium: float) -> tuple[int, str]:
    """カート連動リプライスの目標価格を決める。(target, 理由) を返す。
    - カート保持中 → カートを保てる範囲で値上げ（利益最大化）
    - カート未取得 → 競合がFBMなら同値、FBAならカート価格を下回って奪取
    """
    fba = a["min_fba_comp"]
    fbm = a["min_fbm_comp"]
    cart = a["cart_price"]

    if a["is_winning"]:
        if fba is not None:
            ceiling = fba - step                        # FBA競合より少し下で維持
        elif fbm is not None:
            ceiling = int(fbm * (1 + fbm_premium))       # FBMのみ→上に乗せられる
        else:
            # 競合なし → 上限の基準がなく、毎サイクル上げ続けると価格が暴騰し
            # カート抑制(buy box suppression)で売れなくなる恐れ。維持する。
            return current, f"カート保持・競合なし→維持({current:,}円)"
        target = min(current + raise_step, ceiling)
        if target <= current:
            return current, f"カート保持・値上げ余地なし→維持({current:,}円)"
        return target, f"カート保持→値上げ {current:,}→{target:,}円（上限{ceiling:,}）"

    # カート未取得 → 奪取
    if cart is not None and a["cart_is_fba"] is False:
        # カート保持者が FBM → FBA なら同値で取りやすい（取得後は値上げフェーズで上げる）
        base = fbm if fbm is not None else cart
        return base, f"未取得・カートFBM→同値で奪取 {base:,}円"
    if cart is not None:
        # カート保持者が FBA → 下回って奪取
        return cart - step, f"未取得・カートFBA→カート-{step} {cart - step:,}円"
    if fba is not None:
        # カート無し・FBA競合あり → FBA最安の少し下で奪取
        return fba - step, f"未取得・FBA競合→FBA最安-{step} {fba - step:,}円"
    if fbm is not None:
        # カート無し・FBMのみ → FBA優位。安売りせず維持（次サイクルで様子見）
        return current, "未取得・FBMのみ→FBA優位のため維持（安売りしない）"
    return current, "競合情報なし→維持"


def _batch_write_reprice(sheet_row: int, cart_price: int | None, rival_count: int,
                         new_price: int | None, spreadsheet_id=None):
    """X列(カート価格予想)・Y列(ライバル数) を常に書き込む。
    new_price が指定された場合は J列(販売価格)も更新する。"""
    svc = _sheets_service()
    ssid = _ssid(spreadsheet_id)
    data = [
        {"range": f"{SHEET_NAME}!AC{sheet_row}",
         "values": [[str(cart_price) if cart_price is not None else ""]]},
        {"range": f"{SHEET_NAME}!AD{sheet_row}",
         "values": [[str(rival_count)]]},
    ]
    if new_price is not None:
        data.append({"range": f"{SHEET_NAME}!J{sheet_row}", "values": [[str(new_price)]]})
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=ssid,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()


def run_auto_reprice(dry_run: bool = True, step_yen: int = 10, spreadsheet_id=None):
    """generator: ASIN+コンディション別に同条件FBA最安値を参照して価格調整ログを yield する。

    ロジック:
      target = 同条件FBA最安値 - step_yen
      AE列(最低販売価格)が設定されていれば下限として使用。空の場合は現在価格が下限（デフォルト）。
      AC列にカートに入る価格予想、AD列にFBAライバル数を常に書き込む。
    """
    from sp_api.api import ListingsItems, Products
    from sp_api.base import Marketplaces

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("AE", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "3.出品済み":
            continue
        asin      = _cell(row, COL_ASIN)
        sku       = _cell(row, COL_SKU)
        price_str = _cell(row, COL_HANBAI)
        state     = _cell(row, COL_STATE)
        min_str   = _cell(row, COL_MIN_PRICE)
        if not asin or not sku or not price_str:
            continue
        try:
            current = int(float(price_str.replace(",", "").replace("¥", "")))
        except ValueError:
            continue
        # AE列が空 → 現在価格を下限（デフォルト = 価格を下げない）
        try:
            floor = int(float(min_str.replace(",", ""))) if min_str else current
        except ValueError:
            floor = current
        ct, _ = _CONDITION_MAP.get(state, ("used_good", COL_NOTE_G))
        sub_cond = _SUBCONDITION_MAP.get(ct, "good")
        targets.append({
            "sheet_row":     sheet_row,
            "kanri_id":      _cell(row, COL_KANRI_ID),
            "asin":          asin,
            "sku":           sku,
            "current":       current,
            "floor":         floor,
            "floor_manual":  bool(min_str),
            "sub_condition": sub_cond,
        })

    yield f"調整対象: {len(targets)} 件（3.出品済み）"
    if not targets:
        yield "対象なし。終了します。"
        return

    products_api = Products(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    listings_api = None if dry_run else ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)

    # 同一ASIN は1回だけ API 呼び出し（キャッシュ）
    offers_cache: dict[str, dict | None] = {}
    updated = skipped = failed = 0

    for t in targets:
        floor_label = f"{t['floor']:,}円(手動)" if t["floor_manual"] else f"{t['floor']:,}円(現在価格)"
        yield f"[{t['kanri_id']}] 現在={t['current']:,}円 | {t['sub_condition']} | 下限={floor_label}"

        asin = t["asin"]
        if asin not in offers_cache:
            offers_cache[asin] = _get_item_offers(products_api, asin)
            time.sleep(1)

        min_competitor, rival_count = _analyze_fba_offers(offers_cache[asin], t["sub_condition"])

        if min_competitor is None:
            yield f"  → 同条件の競合出品なし: スキップ（ライバル={rival_count}）"
            _batch_write_reprice(t["sheet_row"], None, rival_count, None, spreadsheet_id)
            skipped += 1
            continue

        target = min_competitor - step_yen
        direction = "↓" if target < t["current"] else "↑" if target > t["current"] else "→"
        yield f"  → 競合最安値={min_competitor:,}円 / ライバル={rival_count} / 目標={target:,}円 {direction}"

        if target < t["floor"]:
            if t["floor_manual"]:
                # 設定価格(AE列)が手動設定されている → その-5%を適用
                target = int(t["floor"] * 0.95)
                yield f"  → 下限({t['floor']:,}円)を下回るため、設定価格-5%={target:,}円に変更"
            else:
                # 下限が現在価格のデフォルト → スキップ
                yield f"  → 下限({t['floor']:,}円 ≒ 現在価格)を下回るためスキップ"
                _batch_write_reprice(t["sheet_row"], target, rival_count, None, spreadsheet_id)
                skipped += 1
                continue

        if target == t["current"]:
            yield f"  → 変更不要（すでに目標価格）"
            _batch_write_reprice(t["sheet_row"], target, rival_count, None, spreadsheet_id)
            skipped += 1
            continue

        if dry_run:
            yield f"  → [ドライラン] {t['current']:,}円 → {target:,}円"
            _batch_write_reprice(t["sheet_row"], target, rival_count, None, spreadsheet_id)
            updated += 1
            continue

        try:
            listings_api.patch_listings_item(
                sellerId=_seller_id(), sku=t["sku"],
                marketplaceIds=[_marketplace_id()],
                body={
                    "productType": "PRODUCT",
                    "patches": [{"op": "replace", "path": "/attributes/purchasable_offer",
                                 "value": [{"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(target)}]}]}]}],
                },
            )
            _batch_write_reprice(t["sheet_row"], target, rival_count, target, spreadsheet_id)
            yield f"  → 更新完了: {t['current']:,}円 → {target:,}円"
            updated += 1
        except Exception as e:
            _batch_write_reprice(t["sheet_row"], target, rival_count, None, spreadsheet_id)
            yield f"  → エラー: {e}"
            failed += 1
        time.sleep(1)

    yield f"完了: 更新 {updated} 件 / スキップ {skipped} 件 / 失敗 {failed} 件"


def run_buybox_reprice(dry_run: bool = True, step_yen: int = 10, raise_step: int = 50,
                       fbm_premium: float = 0.05, spreadsheet_id=None):
    """generator: カート(BuyBox)連動リプライサー。

    - カート保持中(○) → カートを保てる範囲で少しずつ値上げ（利益最大化）
    - カート未取得(✗)  → 競合がFBMなら同値、FBAならカート価格を下回って奪取
    AE列(最低販売価格)を下限とする（空なら現在価格＝値下げしない）。
    毎時サマリー更新と組み合わせ「上げて落ちたら次サイクルで下げ戻す」探り運用が前提。
    """
    from sp_api.api import ListingsItems, Products
    from sp_api.base import Marketplaces

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("AE", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "3.出品済み":
            continue
        asin      = _cell(row, COL_ASIN)
        sku       = _cell(row, COL_SKU)
        price_str = _cell(row, COL_HANBAI)
        state     = _cell(row, COL_STATE)
        min_str   = _cell(row, COL_MIN_PRICE)
        if not asin or not sku or not price_str:
            continue
        try:
            current = int(float(price_str.replace(",", "").replace("¥", "")))
        except ValueError:
            continue
        try:
            floor = int(float(min_str.replace(",", ""))) if min_str else current
        except ValueError:
            floor = current
        ct, _ = _CONDITION_MAP.get(state, ("used_good", COL_NOTE_G))
        sub_cond = _SUBCONDITION_MAP.get(ct, "good")
        targets.append({
            "sheet_row": sheet_row, "kanri_id": _cell(row, COL_KANRI_ID),
            "asin": asin, "sku": sku, "current": current,
            "floor": floor, "floor_manual": bool(min_str), "sub_condition": sub_cond,
        })

    yield f"調整対象: {len(targets)} 件（3.出品済み）"
    yield f"設定: 奪取きざみ={step_yen}円 / 値上げきざみ={raise_step}円 / FBM上乗せ={int(fbm_premium*100)}%"
    if not targets:
        yield "対象なし。終了します。"
        return

    products_api = Products(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    listings_api = None if dry_run else ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)

    offers_cache: dict[str, dict | None] = {}
    raised = took = held = skipped = failed = 0

    for t in targets:
        floor_label = f"{t['floor']:,}円(手動)" if t["floor_manual"] else f"{t['floor']:,}円(現在価格)"
        yield f"[{t['kanri_id']}] 現在={t['current']:,}円 | {t['sub_condition']} | 下限={floor_label}"

        asin = t["asin"]
        if asin not in offers_cache:
            offers_cache[asin] = _get_item_offers(products_api, asin)
            time.sleep(1)

        a = _analyze_offers_smart(offers_cache[asin], t["sub_condition"])
        cart_owner = ("自分" if a["is_winning"]
                      else ("FBA" if a["cart_is_fba"] else "FBM" if a["cart_is_fba"] is False else "なし"))
        cart_p = f"{a['cart_price']:,}円" if a["cart_price"] is not None else "—"
        fba_p  = f"{a['min_fba_comp']:,}" if a["min_fba_comp"] is not None else "—"
        fbm_p  = f"{a['min_fbm_comp']:,}" if a["min_fbm_comp"] is not None else "—"
        cart_mark = "○自分" if a["is_winning"] else "✗"
        yield (f"  状況: カート={cart_mark}({cart_owner}) カート価格={cart_p} / "
               f"FBA競合={a['fba_rival_count']}(最安{fba_p}) / "
               f"FBM競合={a['fbm_rival_count']}(最安{fbm_p})")

        target, reason = _decide_buybox_target(a, t["current"], step_yen, raise_step, fbm_premium)
        yield f"  判断: {reason}"

        rival_total = a["fba_rival_count"] + a["fbm_rival_count"]

        # 下限適用
        if target < t["floor"]:
            if t["floor_manual"]:
                target = int(t["floor"] * 0.95)
                yield f"  → 下限({t['floor']:,}円)を下回るため設定価格-5%={target:,}円"
            else:
                yield f"  → 下限({t['floor']:,}円≒現在価格)を下回るためスキップ"
                _batch_write_reprice(t["sheet_row"], a["cart_price"], rival_total, None, spreadsheet_id)
                skipped += 1
                time.sleep(0.5)
                continue

        if target == t["current"]:
            yield "  → 変更なし（維持）"
            _batch_write_reprice(t["sheet_row"], a["cart_price"], rival_total, None, spreadsheet_id)
            held += 1
            time.sleep(0.5)
            continue

        going_up = target > t["current"]
        if dry_run:
            yield f"  → [ドライラン] {t['current']:,}円 → {target:,}円 {'⤴値上げ' if going_up else '⤵奪取'}"
            _batch_write_reprice(t["sheet_row"], a["cart_price"], rival_total, None, spreadsheet_id)
            if going_up:
                raised += 1
            else:
                took += 1
            time.sleep(0.3)
            continue

        try:
            listings_api.patch_listings_item(
                sellerId=_seller_id(), sku=t["sku"],
                marketplaceIds=[_marketplace_id()],
                body={"productType": "PRODUCT",
                      "patches": [{"op": "replace", "path": "/attributes/purchasable_offer",
                                   "value": [{"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(target)}]}]}]}]},
            )
            _batch_write_reprice(t["sheet_row"], a["cart_price"], rival_total, target, spreadsheet_id)
            yield f"  → 更新完了: {t['current']:,}円 → {target:,}円 {'⤴値上げ' if going_up else '⤵奪取'}"
            if going_up:
                raised += 1
            else:
                took += 1
        except Exception as e:
            _batch_write_reprice(t["sheet_row"], a["cart_price"], rival_total, None, spreadsheet_id)
            yield f"  → エラー: {e}"
            failed += 1
        time.sleep(1)

    yield f"完了: 値上げ {raised} 件 / 奪取(値下げ) {took} 件 / 維持 {held} 件 / スキップ {skipped} 件 / 失敗 {failed} 件"


def run_set_price_from_summary(dry_run: bool = True, summary_spreadsheet_id: str = None, management_spreadsheet_id=None):
    """generator: サマリーシートのP列（変更金額）に入力された値をAmazon出品価格に反映する。

    - A列(SKU) と P列(変更金額) を読む（_SCOL_CHANGE_PRICE=15）
    - ステータスが販売中/納品中の行が対象（B列で判定）
    - dry_run=True のときはログのみ（実際の変更なし）
    - 変更後は G列（販売価格）も書き戻す
    - management_spreadsheet_id が指定されていれば管理シートの J列も更新する
    """
    from sp_api.api import ListingsItems

    if not summary_spreadsheet_id:
        yield "エラー: summary_spreadsheet_id が未指定です"
        return

    yield "サマリーシート読み込み中..."
    svc = _sheets_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=summary_spreadsheet_id,
        range="商品一覧!A2:P4000",
    ).execute()
    rows = result.get("values", [])

    targets = []
    for i, row in enumerate(rows, start=2):  # 行番号（ヘッダーが1行目）
        sku      = row[_SCOL_SKU].strip()    if len(row) > _SCOL_SKU    else ""
        status   = row[_SCOL_STATUS].strip() if len(row) > _SCOL_STATUS else ""
        min_str  = row[_SCOL_CHANGE_PRICE].strip() if len(row) > _SCOL_CHANGE_PRICE else ""
        if not sku or not min_str or status not in ("販売中", "納品中"):
            continue
        try:
            new_price = int(float(min_str.replace(",", "").replace("¥", "")))
        except ValueError:
            continue
        hanbai_str = row[_SCOL_HANBAI].strip() if len(row) > _SCOL_HANBAI else ""
        try:
            current = int(float(hanbai_str.replace(",", ""))) if hanbai_str else None
        except ValueError:
            current = None
        targets.append({
            "summary_row": i,
            "sku":         sku,
            "current":     current,
            "new_price":   new_price,
        })

    yield f"対象: {len(targets)} 件（販売中/納品中 かつ P列(変更金額)入力済み）"
    if not targets:
        yield "対象なし。P列（変更金額）に金額を入力してください。"
        return

    from sp_api.base import Marketplaces
    listings_api = None if dry_run else ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)

    # SKU→管理シート行番号のマップ（管理シート更新用）
    mgmt_sku_row: dict[str, int] = {}
    if not dry_run and management_spreadsheet_id:
        mgmt_rows = _read_rows("O", management_spreadsheet_id)
        for sheet_row, row in mgmt_rows:
            sku_val = _cell(row, COL_SKU)
            if sku_val:
                mgmt_sku_row[sku_val] = sheet_row

    updated = skipped = failed = 0
    for t in targets:
        current_label = f"{t['current']:,}円" if t["current"] else "不明"
        yield f"[{t['sku']}] 現在={current_label} → {t['new_price']:,}円"

        if t["current"] == t["new_price"]:
            yield f"  → 変更不要（すでに同じ価格）"
            skipped += 1
            continue

        if dry_run:
            yield f"  → [ドライラン] {current_label} → {t['new_price']:,}円"
            updated += 1
            continue

        try:
            listings_api.patch_listings_item(
                sellerId=_seller_id(), sku=t["sku"],
                marketplaceIds=[_marketplace_id()],
                body={
                    "productType": "PRODUCT",
                    "patches": [{"op": "replace", "path": "/attributes/purchasable_offer",
                                 "value": [{"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(t["new_price"])}]}]}]}],
                },
            )
            # サマリーシートの G列（販売価格）を更新（G = index 6 = 販売価格）
            svc.spreadsheets().values().update(
                spreadsheetId=summary_spreadsheet_id,
                range=f"商品一覧!G{t['summary_row']}",
                valueInputOption="RAW",
                body={"values": [[str(t["new_price"])]]},
            ).execute()
            # 管理シートの J列（販売価格）も更新
            if management_spreadsheet_id and t["sku"] in mgmt_sku_row:
                svc.spreadsheets().values().update(
                    spreadsheetId=management_spreadsheet_id,
                    range=f"{SHEET_NAME}!J{mgmt_sku_row[t['sku']]}",
                    valueInputOption="RAW",
                    body={"values": [[str(t["new_price"])]]},
                ).execute()
            yield f"  → 更新完了: {current_label} → {t['new_price']:,}円"
            updated += 1
        except Exception as e:
            yield f"  → エラー: {e}"
            failed += 1
        time.sleep(1)

    yield f"完了: 更新 {updated} 件 / スキップ {skipped} 件 / 失敗 {failed} 件"


def run_set_price_from_min(dry_run: bool = True, spreadsheet_id=None):
    """generator: AE列（最低販売価格）に入力された値をそのままAmazon出品価格に設定する。

    - ステータスが「3.出品済み」かつ AE列が空でない行が対象
    - dry_run=True のときはログのみ（実際の変更なし）
    - 変更後は J列（販売価格）もスプレッドシートに書き戻す
    """
    from sp_api.api import ListingsItems

    yield "スプレッドシート読み込み中..."
    all_rows = _read_rows("AE", spreadsheet_id)

    targets = []
    for sheet_row, row in all_rows:
        if _cell(row, COL_STATUS) != "3.出品済み":
            continue
        sku      = _cell(row, COL_SKU)
        price_str = _cell(row, COL_HANBAI)
        min_str   = _cell(row, COL_MIN_PRICE)
        if not sku or not min_str:
            continue
        try:
            new_price = int(float(min_str.replace(",", "").replace("¥", "")))
        except ValueError:
            continue
        try:
            current = int(float(price_str.replace(",", "").replace("¥", ""))) if price_str else None
        except ValueError:
            current = None
        targets.append({
            "sheet_row": sheet_row,
            "kanri_id":  _cell(row, COL_KANRI_ID),
            "sku":       sku,
            "current":   current,
            "new_price": new_price,
        })

    yield f"対象: {len(targets)} 件（3.出品済み かつ 最低販売価格入力済み）"
    if not targets:
        yield "対象なし。終了します。"
        return

    from sp_api.base import Marketplaces
    listings_api = None if dry_run else ListingsItems(credentials=_sp_creds(), marketplace=Marketplaces.JP)
    updated = skipped = failed = 0

    for t in targets:
        current_label = f"{t['current']:,}円" if t["current"] else "不明"
        yield f"[{t['kanri_id']}] 現在={current_label} → 最低販売価格={t['new_price']:,}円"

        if t["current"] == t["new_price"]:
            yield f"  → 変更不要（すでに同じ価格）"
            skipped += 1
            continue

        if dry_run:
            yield f"  → [ドライラン] {current_label} → {t['new_price']:,}円"
            updated += 1
            continue

        try:
            listings_api.patch_listings_item(
                sellerId=_seller_id(), sku=t["sku"],
                marketplaceIds=[_marketplace_id()],
                body={
                    "productType": "PRODUCT",
                    "patches": [{"op": "replace", "path": "/attributes/purchasable_offer",
                                 "value": [{"currency": "JPY", "our_price": [{"schedule": [{"value_with_tax": float(t["new_price"])}]}]}]}],
                },
            )
            # J列（販売価格）をスプレッドシートに書き戻す
            _sheets_service().spreadsheets().values().update(
                spreadsheetId=_ssid(spreadsheet_id),
                range=f"{SHEET_NAME}!J{t['sheet_row']}",
                valueInputOption="RAW",
                body={"values": [[str(t["new_price"])]]},
            ).execute()
            yield f"  → 更新完了: {current_label} → {t['new_price']:,}円"
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
    import os

    FONT = "NotoSansJP"
    if FONT not in pdfmetrics.getRegisteredFontNames():
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

    # 管理ID・日付（小さめ）
    c.setFont(FONT, 9)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(margin, H - 14 * mm, f"{item['kanri_id']}  /  {today}")

    # 商品名
    c.setFont(FONT, 12)
    c.setFillColorRGB(0, 0, 0)
    name = item["item_name"][:52] + ("…" if len(item["item_name"]) > 52 else "")
    c.drawString(margin, H - 24 * mm, name)

    # コンディション
    c.setFont(FONT, 10)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.drawString(margin, H - 33 * mm, f"コンディション: {item['condition_jp']}")

    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.setLineWidth(0.5)
    c.line(margin, H - 37 * mm, W - margin, H - 37 * mm)

    # バーコード（中央寄せ、大きめ）
    bc_w, bc_h = 140 * mm, 60 * mm
    bc_x = (W - bc_w) / 2
    bc_y = H / 2 - bc_h / 2
    if item["fnsku"]:
        try:
            bc_img = ImageReader(_barcode_png(item["fnsku"]))
            c.drawImage(bc_img, bc_x, bc_y, width=bc_w, height=bc_h,
                        preserveAspectRatio=True, anchor="c")
        except Exception:
            pass

    # FNSKU テキスト（バーコード直下）
    c.setFont(FONT, 13)
    c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(W / 2, bc_y - 10 * mm, item["fnsku"] or "(FNSKU未取得)")

    # フッター
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.line(margin, 18 * mm, W - margin, 18 * mm)
    c.setFont(FONT, 8)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(margin, 11 * mm, item["sku"])
    c.drawRightString(W - margin, 11 * mm, item.get("asin", ""))


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

        log(f"[{t['kanri_id']}] FNSKU={fnsku or '未取得'}")
        items_for_pdf.append({
            **t,
            "fnsku": fnsku, "item_name": item_name,
            "condition_type": ct,
            "condition_jp": _CONDITION_JP.get(ct, ct),
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
    confirmation_ids: dict[str, str] = {}  # sh... → FBA15... の対応表
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
                confirmation_ids[ship_id] = confirm_id

    log_fn("  ✅ プラン作成完了！（配置確定まで完了）")

    return {"plan_id": plan_id, "placement_option_id": p_id, "shipment_ids": ship_ids, "confirmation_ids": confirmation_ids}


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
        # Drive に画像がない SKU はスキップ
        folder_id = _find_sku_folder(sku)
        if not folder_id or not _list_images(folder_id, max_count=1):
            kanri_id = _cell(row, COL_KANRI_ID)
            log(f"  [{kanri_id}] {sku} → Drive に画像なし。スキップ")
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

    log(f"対象: {len(targets)} 件（2.写真撮影済み かつ Drive に画像あり）")
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
        })
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
        # V列: FBA出荷確認ID（FBA15...形式）→ 受取確認で使用
        fba_id = ""
        if plan_result_obj:
            conf_ids = plan_result_obj.get("confirmation_ids", {})
            fba_id = next(iter(conf_ids.values()), "")

        updated = 0
        for it in items_for_pdf:
            if not it.get("fnsku") or it["fnsku"] == "X00000DRY":
                continue
            sku = it["sku"]
            matched = [t for t in targets if t["sku"] == sku]
            for t in matched:
                _update_cell(t["sheet_row"], "D", "3.発送待ち", spreadsheet_id)
                if fba_id:
                    _update_cell(t["sheet_row"], "V", fba_id, spreadsheet_id)
                log(f"  [{t['kanri_id']}] → 3.発送待ち" + (f"  FBA ID: {fba_id}" if fba_id else ""))
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
    ready_to_ship_start: str = "",
    ready_to_ship_end: str = "",
) -> dict:
    """輸送オプションを生成・取得する（NON_PARTNERED_SPD 固定）。
    ready_to_ship_start/end: ISO 8601 UTC (例: "2026-06-15T00:00:00Z")。省略時は翌日〜14日後。
    Returns: {"options": [...], "error": None} or {"options": [], "error": "message"}
    """
    import requests as _req
    from datetime import datetime, timezone, timedelta

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

    # readyToShipWindow（必須）: 指定なければ翌日〜14日後
    now = datetime.now(timezone.utc)
    if not ready_to_ship_start:
        ready_to_ship_start = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not ready_to_ship_end:
        ready_to_ship_end = (now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    configurations = []
    for ship_id in shipment_ids:
        cfg = {
            "shipmentId": ship_id,
            "shippingMode": "NON_PARTNERED_SPD",
            "readyToShipWindow": {
                "start": ready_to_ship_start,
                "end": ready_to_ship_end,
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
                "403 Forbidden: 輸送方法の生成に必要な権限がありません。\n\n"
                "【小口発送（ヤマト・佐川）の場合】このステップは不要です。"
                "① のログに表示された「📦 発送先FC住所」へ直接発送してください。\n\n"
                "【大口貨物（LTL）の場合】Seller Central から輸送方法を選択してください。\n"
                "（権限申請: SP-API デベロッパーコンソールで inbound_shipment_transport_write スコープを申請）"
            ),
        }

    if r.status_code != 202:
        errs = r.json().get("errors", [{}])
        # WARNING だけで ERROR がなければ続行（LTL pallet 警告など）
        actual_errors = [e for e in errs if e.get("message", "").startswith("ERROR:")]
        if actual_errors:
            msg = actual_errors[0].get("message", r.text[:300])
            return {"options": [], "error": f"{r.status_code}: {msg}"}
        # WARNING のみ → 続行（202 相当として扱う）

    op_id = r.json().get("operationId", "") if r.status_code == 202 else ""
    if op_id:
        _fba_wait_op(BASE, headers, op_id, lambda _: None, timeout=60)

    # GET に placementOptionId クエリパラメータが必須
    r2 = _req.get(
        f"{BASE}/inboundPlans/{plan_id}/transportationOptions",
        headers=headers,
        params={"placementOptionId": placement_option_id},
        timeout=15,
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


# ============================================================
# 商品サマリースプレッドシート作成 / 更新
# ============================================================

_SUMMARY_ACCOUNTS = {
    "sato": "1Xb66vv997dWX9CIofuPNY23tuIQwoNFmm-hNBLbnBYo",
    "kudo": "1keLLdpDRu2l9AjHyM6qRe_W8FFH_Jtl-isb1XFp8MzA",
}

# Amazon API のコンディションコード → 日本語表記
_CONDITION_JA = {
    # FBA inventory API が返す形式（複数パターン対応）
    "used_very_good":  "非常に良い",
    "used_good":       "良い",
    "used_acceptable": "可",
    "new":             "新品",
    "used":            "中古",
    "usedlikebew":     "ほぼ新品",
    "UsedLikeNew":     "ほぼ新品",
    "UsedVeryGood":    "非常に良い",
    "UsedGood":        "良い",
    "UsedAcceptable":  "可",
    "NewItem":         "新品",
    "USED_LIKE_NEW":   "ほぼ新品",
    "USED_VERY_GOOD":  "非常に良い",
    "USED_GOOD":       "良い",
    "USED_ACCEPTABLE": "可",
    "NEW":             "新品",
    "USED":            "中古",
}

def _kanri_id_from_sku(sku: str) -> str:
    """SKUから管理IDを抽出する。
    RS00011ST12000260512 → RS00011ST （末尾の価格+6桁日付を除去）
    RS KT00092 → RS KT00092 （数字が10桁未満なら変換しない）
    """
    import re
    m = re.match(r'^(.+?)\d{10,}$', sku)
    return m.group(1) if m else sku

def _price_from_sku(sku: str, kanri_id: str) -> str:
    """SKUに埋め込まれた販売価格を取り出す。
    RS00011ST12000260512, kanri_id=RS00011ST → 12000
    """
    if not kanri_id or not sku.startswith(kanri_id):
        return ""
    suffix = sku[len(kanri_id):]   # 例: 12000260512
    if len(suffix) < 7:            # 最低 1桁価格 + 6桁日付
        return ""
    return suffix[:-6]             # 末尾6桁（YYMMDD）を除いた残りが価格

# 商品一覧シートの列順（スプレッドシート側の列順と一致させること・列順変更禁止）
# A       B         C     D   E        F          G      H        I       J
# SKU ステータス 商品名 写真 商品ページ コンディション 販売価格 カート獲得 カート価格 カート状態
# K            L     M           N          O              P      Q    R       S    T    U     V    W
# カート価格予想 最低価格 最低価格状態 FBAライバル数 同コンFBA最低金額 変更金額 ASIN アカウント FBA納品日 出品日 売却日 仕入れ値 仕入れ日
_SUMMARY_HEADERS = [
    "SKU", "ステータス", "商品名", "写真", "商品ページ",
    "コンディション", "販売価格", "カート獲得", "カート価格", "カート状態",
    "カート価格予想", "最低価格", "最低価格状態", "FBAライバル数", "同コンFBA最低金額", "変更金額",
    "ASIN", "アカウント",
    "FBA納品日", "出品日", "売却日",
    "仕入れ値", "仕入れ日",
]
# 列インデックス（0始まり）
_SCOL_SKU          = 0   # A
_SCOL_STATUS       = 1   # B
_SCOL_NAME         = 2   # C: 商品名
_SCOL_COND         = 5   # F
_SCOL_HANBAI       = 6   # G
_SCOL_CART         = 7   # H: カート獲得（○/△/✗）
_SCOL_CART_PRICE   = 8   # I: カート価格
_SCOL_FBA_MIN      = 14  # O: 同コンFBA最低金額（同コンディション・FBA最安値）
_SCOL_CHANGE_PRICE = 15  # P: 変更金額（ユーザーが設定する変更後価格）

_CART_HISTORY_SHEET = "カート履歴"
_CART_HISTORY_HEADERS = ["日時", "対象数", "○獲得", "△", "✗未獲得", "獲得率%"]


def _discord_webhook_url():
    """Discord Webhook URL を環境変数 or st.secrets から取得（未設定なら None）。"""
    import os
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        return url
    try:
        return st.secrets["discord"]["webhook_url"]
    except Exception:
        return None


def _post_discord(content: str) -> bool:
    """Discord Webhook にメッセージを投稿。未設定/失敗時は False。"""
    url = _discord_webhook_url()
    if not url:
        return False
    try:
        import json as _json
        import urllib.request
        data = _json.dumps({"content": content[:1900]}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                # Discord(Cloudflare)は User-Agent 無しのリクエストを403で拒否するため必須
                "User-Agent": "sedori-tool-webhook/1.0",
            },
        )
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


def _append_cart_history(svc, ss_id: str, row: list):
    """カート履歴タブに1行追記する（タブが無ければ作成しヘッダーを付ける）。"""
    try:
        svc.spreadsheets().values().append(
            spreadsheetId=ss_id, range=f"{_CART_HISTORY_SHEET}!A1",
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
    except Exception:
        # タブ未作成 → 作成してヘッダー＋1行目を書く
        try:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=ss_id,
                body={"requests": [{"addSheet": {"properties": {"title": _CART_HISTORY_SHEET}}}]},
            ).execute()
            svc.spreadsheets().values().update(
                spreadsheetId=ss_id, range=f"{_CART_HISTORY_SHEET}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [_CART_HISTORY_HEADERS, row]},
            ).execute()
        except Exception:
            pass


def get_cart_dashboard_data(summary_spreadsheet_id: str) -> dict:
    """カート獲得ダッシュボード用データを返す。
    返り値: won/partial/lost/rate, near(あと一歩で獲得=✗だが僅差, gap昇順), history(獲得率推移)"""
    svc = _sheets_service()
    res = {"won": 0, "partial": 0, "lost": 0, "rate": 0.0, "near": [], "history": []}

    def _num(v):
        try:
            return int(float(str(v).replace(",", "").replace("¥", "").strip()))
        except Exception:
            return None

    try:
        rows = svc.spreadsheets().values().get(
            spreadsheetId=summary_spreadsheet_id, range="商品一覧!A2:P5000",
        ).execute().get("values", [])
    except Exception:
        rows = []

    for r in rows:
        status = r[_SCOL_STATUS] if len(r) > _SCOL_STATUS else ""
        if status not in ("販売中", "納品中"):
            continue
        bb = r[_SCOL_CART].strip() if len(r) > _SCOL_CART and r[_SCOL_CART] else ""
        if bb == "○":
            res["won"] += 1
        elif bb == "△":
            res["partial"] += 1
        elif bb == "✗":
            res["lost"] += 1
        else:
            continue
        if bb == "✗":
            price = _num(r[_SCOL_HANBAI]) if len(r) > _SCOL_HANBAI else None
            cart  = _num(r[_SCOL_CART_PRICE]) if len(r) > _SCOL_CART_PRICE else None
            if price is not None and cart is not None and price > cart:
                res["near"].append({
                    "name":  (str(r[_SCOL_NAME])[:40] if len(r) > _SCOL_NAME else ""),
                    "sku":   (r[_SCOL_SKU] if len(r) > _SCOL_SKU else ""),
                    "price": price, "cart": cart, "gap": price - cart,
                })

    total = res["won"] + res["partial"] + res["lost"]
    res["rate"] = round(res["won"] / total * 100, 1) if total else 0.0
    res["near"].sort(key=lambda x: x["gap"])
    res["near"] = res["near"][:15]

    try:
        res["history"] = svc.spreadsheets().values().get(
            spreadsheetId=summary_spreadsheet_id, range=f"{_CART_HISTORY_SHEET}!A2:F2000",
        ).execute().get("values", [])
    except Exception:
        pass
    return res


def run_create_summary_sheet(out_spreadsheet_id: str | None = None):
    """generator: Amazon APIのみをベースにしたサマリーシートを作成/更新する。

    データソース（Amazon API が主役）:
      - FBA Inventory API        → 販売中（在庫あり）
      - FBA Inbound ACTIVE       → 納品中
      - FBA Inbound CLOSED       → FBA納品日（ShipmentNameから日付パース）
      - CatalogItems API         → Amazon商品画像
      - Orders API (1年)         → 売却済み
      スプレッドシートは仕入れ値など補足データのみに使用。
    """
    import datetime as dt
    import re
    from sp_api.api import Inventories, Orders as OrdersAPI, Products, FulfillmentInboundV0, CatalogItems
    from sp_api.base import Marketplaces

    svc = _sheets_service()
    creds = _sp_creds()

    # ── Step 1: FBA Inventory（販売中） ─────────────────────────────────
    yield "① Amazon FBA在庫を取得中..."
    inv_api = Inventories(credentials=creds, marketplace=Marketplaces.JP)
    fba_items: dict[str, dict] = {}  # sku -> {asin, product_name, condition, last_updated, total_qty}
    try:
        resp = inv_api.get_inventory_summary_marketplace(
            marketplaceId=_marketplace_id(), details=True
        )
        for item in resp.payload.get("inventorySummaries", []):
            sku = item.get("sellerSku", "")
            if not sku:
                continue
            det = item.get("inventoryDetails", {})
            fulfillable = det.get("fulfillableQuantity", 0)
            fba_items[sku] = {
                "asin":           item.get("asin", ""),
                "product_name":   item.get("productName", ""),
                "condition":      item.get("condition", ""),
                "last_updated":   (item.get("lastUpdatedTime") or "")[:10],
                "total_qty":      item.get("totalQuantity", 0),
                "fulfillable_qty": fulfillable,  # 実際に販売可能な数量
            }
    except Exception as e:
        yield f"FBA在庫取得エラー: {e}"
    yield f"  → {len(fba_items)} SKU"

    # ── Step 2: FBA Inbound（納品中） ────────────────────────────────────
    yield "② FBA納品中シップメントを取得中..."
    inbound_api = FulfillmentInboundV0(credentials=creds, marketplace=Marketplaces.JP)
    inbound_skus: dict[str, dict] = {}  # sku -> {asin, product_name, shipment_id, created_date}
    active_statuses = ["WORKING", "SHIPPED", "IN_TRANSIT", "RECEIVING", "CHECKED_IN"]
    try:
        resp = inbound_api.get_shipments(
            QueryType="SHIPMENT",
            ShipmentStatusList=active_statuses,
            MarketplaceId=_marketplace_id(),
        )
        for shipment in resp.payload.get("ShipmentData", []):
            shipment_id = shipment.get("ShipmentId", "")
            shipment_status = shipment.get("ShipmentStatus", "")
            try:
                items_resp = inbound_api.shipment_items(
                    ShipmentId=shipment_id,
                    MarketplaceId=_marketplace_id(),
                )
                for it in items_resp.payload.get("ItemData", []):
                    sku = it.get("SellerSKU", "")
                    if sku and sku not in inbound_skus:
                        inbound_skus[sku] = {
                            "asin":            it.get("FulfillmentNetworkSKU", ""),
                            "product_name":    "",
                            "shipment_id":     shipment_id,
                            "shipment_status": shipment_status,
                        }
                time.sleep(0.3)
            except Exception:
                pass
    except Exception as e:
        yield f"FBA Inbound取得エラー: {e}"
    # FBA在庫にある = 受け取り済みなのでinboundから除外
    for sku in list(inbound_skus.keys()):
        if sku in fba_items:
            del inbound_skus[sku]
    yield f"  → {len(inbound_skus)} SKU（納品中）"

    # ── Step 2b: FBA納品日マップ（FBA在庫の lastUpdatedTime を利用）─────
    # shipment_items_by_shipment はネットワークハングが発生するため使わない。
    # FBA在庫APIの lastUpdatedTime（在庫が最後に更新された日時）で代替する。
    fba_date_map: dict[str, str] = {}  # kanri_id → 日付文字列
    for sku, data in fba_items.items():
        kid = _kanri_id_from_sku(sku)
        lu = data.get("last_updated", "")  # 例: "2026-05-26T..."
        if lu and kid not in fba_date_map:
            fba_date_map[kid] = lu[:10]   # YYYY-MM-DD 部分だけ
    yield f"② FBA納品日: {len(fba_date_map)} 件取得（FBA在庫更新日時より）"

    # ── Step 3: 注文履歴（売却済み） ────────────────────────────────────
    yield "③ 注文履歴を取得中（過去1年）..."
    orders_api = OrdersAPI(credentials=creds, marketplace=Marketplaces.JP)
    created_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sold_skus: dict[str, dict] = {}
    try:
        resp = orders_api.get_orders(
            CreatedAfter=created_after,
            MarketplaceIds=[_marketplace_id()],
            FulfillmentChannels=["AFN"],
        )
        order_list = list(resp.payload.get("Orders", []))
        nt = resp.payload.get("NextToken")
        while nt:
            resp = orders_api.get_orders(NextToken=nt)
            order_list.extend(resp.payload.get("Orders", []))
            nt = resp.payload.get("NextToken")
            time.sleep(0.3)
        yield f"  注文 {len(order_list)} 件。SKU照合中..."
        for order in order_list:
            if order.get("OrderStatus") == "Canceled":
                continue
            order_id      = order.get("AmazonOrderId", "")
            purchase_date = order.get("PurchaseDate", "")[:10]
            try:
                ir = orders_api.get_order_items(order_id)
                for oi in ir.payload.get("OrderItems", []):
                    sku = oi.get("SellerSKU", "")
                    if sku and sku not in sold_skus:
                        ip = oi.get("ItemPrice", {})
                        sold_price = ip.get("Amount", "") if isinstance(ip, dict) else ""
                        sold_skus[sku] = {
                            "purchase_date": purchase_date,
                            "price": str(int(float(sold_price))) if sold_price else "",
                        }
                time.sleep(0.3)
            except Exception:
                pass
    except Exception as e:
        yield f"注文取得エラー: {e}"
    yield f"  → {len(sold_skus)} SKU（売却済み）"

    # ── Step 4: Amazonベースで全SKUを確定（スプレッドシートは補足のみ） ──
    # Amazon側にある商品だけが対象。スプレッドシートだけの商品は除外。
    amazon_skus = set(fba_items) | set(inbound_skus) | set(sold_skus)
    yield f"④ Amazon商品数: {len(amazon_skus)} SKU。スプレッドシートで補足中..."

    # kanri_master: 管理ID（列A）をキーにしてスプレッドシートの補足情報を格納
    # SKUはAmazonが更新するたびに価格+日付が変わるため、管理IDでマッチングする
    kanri_master: dict[str, dict] = {}
    for account_name, ssid in _SUMMARY_ACCOUNTS.items():
        try:
            result = svc.spreadsheets().values().get(
                spreadsheetId=ssid, range="商品管理!A2:AE4000",
            ).execute()
            for row in result.get("values", []):
                def c(i, r=row): return r[i].strip() if i < len(r) and r[i] else ""
                kanri_id = c(0)   # A列: 管理ID（RS00011ST など）
                if not kanri_id:
                    continue
                kanri_master[kanri_id] = {
                    "account":     account_name,
                    "asin_ss":     c(15),  # P列: ASIN（売却済みで在庫APIにない場合の補完）
                    "hanbai":      c(9),   # J列: 販売価格
                    "shiire":      c(5),   # F列: 仕入れ値
                    "shiire_date": c(4),   # E列: 仕入れ日
                    "min_price":   c(30),  # AE列: 最低販売価格
                    "state":       c(17),  # R列: 状態（コンディション判定用）
                }
        except Exception as e:
            yield f"スプレッドシート({account_name})読み込みエラー: {e}"
    yield f"  スプレッドシート補足: {len(kanri_master)} 管理ID"

    # ── Step 4.5: 自分の出品価格を一括取得（販売中・納品中 SKU、最大20件ずつ）──
    # Products.get_price(ItemType="Sku") → 0.5 req/s、最大20 SKU/リクエスト
    listing_price_map: dict[str, str] = {}  # sku → 現在の出品価格
    active_skus = [sku for sku, d in fba_items.items() if d.get("total_qty", 0) > 0]
    active_skus += list(inbound_skus.keys())
    if active_skus:
        products_api_price = Products(credentials=creds, marketplace=Marketplaces.JP)
        for i in range(0, len(active_skus), 20):
            batch = active_skus[i : i + 20]
            for attempt in range(4):
                try:
                    resp = products_api_price.get_product_pricing_for_skus(
                        seller_sku_list=batch, MarketplaceId=_marketplace_id()
                    )
                    items = resp.payload if isinstance(resp.payload, list) else [resp.payload]
                    for item in items:
                        if item.get("status") != "Success":
                            continue
                        sku_val = item.get("SellerSKU", "")
                        for offer in item.get("Product", {}).get("Offers", []):
                            lp = offer.get("BuyingPrice", {}).get("ListingPrice", {}).get("Amount")
                            if lp is not None:
                                listing_price_map[sku_val] = str(int(lp))
                                break
                    time.sleep(2.5)
                    break
                except Exception as e:
                    if "QuotaExceeded" in str(e) and attempt < 3:
                        time.sleep(10 * (attempt + 1))
                    else:
                        yield f"  出品価格取得エラー: {e}"
                        break
    yield f"  → 出品価格取得: {len(listing_price_map)} 件"

    # ── Step 5: 価格・写真・行データ組み立て ────────────────────────────
    yield f"⑤ 価格・写真を取得中... (販売中={sum(1 for s in fba_items.values() if s.get('fulfillable_qty', s.get('total_qty',0))>0)}, 納品中={len(inbound_skus)}, 売却済み={len(sold_skus)})"
    if not amazon_skus:
        yield "⚠️ Amazon APIから商品が0件でした。認証エラーか在庫・注文が存在しない可能性があります。"
    products_api = Products(credentials=creds, marketplace=Marketplaces.JP)
    cat_api      = CatalogItems(credentials=creds, marketplace=Marketplaces.JP)
    offers_cache: dict[str, dict | None] = {}  # sku → payload（get_listings_offer はSKU単位）
    image_cache:  dict[str, str]         = {}  # asin → image URL
    all_rows = []

    _COND_TO_SUBCOND = {
        "used_very_good": "very_good", "USED_VERY_GOOD": "very_good", "UsedVeryGood": "very_good",
        "used_good":      "good",      "USED_GOOD":      "good",      "UsedGood":      "good",
        "used_acceptable":"acceptable","USED_ACCEPTABLE":"acceptable","UsedAcceptable":"acceptable",
        "new":            "new",       "NEW":            "new",       "NewItem":       "new",
    }

    for sku in sorted(amazon_skus):
        fba     = fba_items.get(sku, {})
        inbound = inbound_skus.get(sku, {})
        sold    = sold_skus.get(sku, {})

        # 管理IDでスプレッドシート補足データを取得（SKUに埋め込まれた管理IDを抽出）
        kanri_id = _kanri_id_from_sku(sku)
        master   = kanri_master.get(kanri_id, {})

        # ASIN: FBA在庫API優先、次にスプレッドシートのP列（売却済みで在庫なしの場合）
        asin         = fba.get("asin") or inbound.get("asin") or master.get("asin_ss", "") or ""
        product_name = fba.get("product_name") or inbound.get("product_name") or ""

        # FBA納品日: CLOSEDシップメントから取得した kanri_id → 日付マップを参照
        fba_date = fba_date_map.get(kanri_id, "")

        # ステータス・日付
        # fulfillable_qty = 実際に販売可能な数量（total_qty はinbound含むため使わない）
        total_qty      = fba.get("total_qty", 0)
        fulfillable_qty = fba.get("fulfillable_qty", total_qty)  # detailsなしの場合はtotalで代替
        if fulfillable_qty > 0:
            status       = "販売中"
            listing_date = fba.get("last_updated", "")
            sold_date    = ""
        elif inbound or total_qty > 0:
            # inbound_skus にある or FBA在庫あり(fulfillable=0) → 納品中
            status       = "納品中"
            listing_date = ""
            sold_date    = ""
        elif sold:
            status       = "売却済み"
            listing_date = fba.get("last_updated", "")
            sold_date    = sold.get("purchase_date", "")
        else:
            status       = "在庫切れ"
            listing_date = sold_date = ""

        # コンディション: FBA APIの値を日本語に変換
        cond_raw   = fba.get("condition", "")
        cond_label = _CONDITION_JA.get(cond_raw) or _CONDITION_JA.get(cond_raw.lower()) or ""
        if not cond_label:
            state = master.get("state", "")
            ct, _ = _CONDITION_MAP.get(state, ("used_good", COL_NOTE_G))
            cond_label = _CONDITION_JA.get(ct, "良い")
        # sub_cond: FBA APIのconditionから直接導出、なければスプレッドシート由来
        sub_cond = _COND_TO_SUBCOND.get(cond_raw, "")
        if not sub_cond:
            state    = master.get("state", "")
            ct, _    = _CONDITION_MAP.get(state, ("used_good", COL_NOTE_G))
            sub_cond = _SUBCONDITION_MAP.get(ct, "good")

        # カート価格・最低価格・FBAライバル数（QuotaExceeded リトライ付き）
        cart_price_actual = cart_cond_str = ""
        cart_price_yoso   = rival_count   = ""
        lowest_price_str  = lowest_cond_str = ""
        fba_min_str   = ""
        buybox_status = ""
        if status in ("販売中", "納品中", "在庫切れ") and sku:
            if sku not in offers_cache:
                payload = None
                for attempt in range(3):
                    try:
                        # get_listings_offer はSKU単位で呼び出し。MyOffer=true で自社出品を識別できる
                        resp = products_api.get_listings_offer(
                            seller_sku=sku, item_condition="Used", MarketplaceId=_marketplace_id()
                        )
                        payload = resp.payload
                        break
                    except Exception as e:
                        if "QuotaExceeded" in str(e) and attempt < 2:
                            time.sleep(6 * (attempt + 1))  # 6s, 12s
                        else:
                            break
                offers_cache[sku] = payload
                time.sleep(2.5)  # SP-API pricing: 0.5 req/s 上限
            c_price, c_cond, low_p, low_cond, rival_cnt, fba_min, winning, has_new_bb, my_offer = _extract_offer_details(
                offers_cache.get(sku), sub_cond
            )
            if c_price is not None:
                cart_price_actual = str(c_price)
            cart_cond_str = c_cond
            if low_p is not None:
                lowest_price_str = str(low_p)
            lowest_cond_str = low_cond
            rival_count = str(rival_cnt)
            if fba_min is not None:
                cart_price_yoso = str(fba_min - 10)
                fba_min_str = str(fba_min)
            # ○: 自分がカート獲得
            # △: 新品がメインカートで自分は中古として出品中
            # ✗: カート未獲得
            if winning:
                buybox_status = "○"
            elif my_offer and has_new_bb:
                buybox_status = "△"
            elif my_offer:
                buybox_status = "✗"
            else:
                buybox_status = "✗"

        # Amazon商品画像（CatalogItems API）
        photo_formula = ""
        if asin:
            if asin not in image_cache:
                try:
                    r = cat_api.get_catalog_item(
                        asin=asin,
                        marketplaceIds=[_marketplace_id()],
                        includedData=["images"],
                    )
                    img_url = ""
                    for img_set in r.payload.get("images", []):
                        if img_set.get("marketplaceId") == _marketplace_id():
                            main_imgs = [i for i in img_set.get("images", [])
                                         if i.get("variant") == "MAIN"]
                            if main_imgs:
                                best = max(main_imgs, key=lambda i: i.get("height", 0))
                                img_url = best.get("link", "")
                            break
                    image_cache[asin] = img_url
                    time.sleep(0.5)
                except Exception:
                    image_cache[asin] = ""
            url = image_cache.get(asin, "")
            if url:
                photo_formula = f'=IMAGE("{url}")'

        page_link = f'=HYPERLINK("https://www.amazon.co.jp/dp/{asin}", "{asin}")' if asin else ""

        # 販売価格: Amazon APIを正とする
        # 販売中/納品中 → Products.get_price で取得した現在の出品価格
        # 売却済み     → Orders APIの実際の売却価格
        # フォールバック → SKU末尾に埋め込まれた価格
        if status == "売却済み":
            hanbai = sold.get("price", "") or _price_from_sku(sku, kanri_id)
        else:
            hanbai = listing_price_map.get(sku, "") or _price_from_sku(sku, kanri_id)

        all_rows.append([
            sku,                          # A: SKU
            status,                       # B: ステータス
            product_name,                 # C: 商品名
            photo_formula,                # D: 写真
            page_link,                    # E: 商品ページ
            cond_label,                   # F: コンディション
            hanbai,                       # G: 販売価格
            buybox_status,                # H: カート獲得（○/△/✗）
            cart_price_actual,            # I: カート価格
            cart_cond_str,                # J: カート状態
            cart_price_yoso,              # K: カート価格予想
            lowest_price_str,             # L: 最低価格
            lowest_cond_str,              # M: 最低価格状態
            rival_count,                  # N: FBAライバル数
            fba_min_str,                  # O: 同コンFBA最低金額
            master.get("min_price", ""),  # P: 変更金額（ユーザー手動入力）
            asin,                         # Q: ASIN
            master.get("account", ""),    # R: アカウント
            fba_date,                     # S: FBA納品日
            listing_date,                 # T: 出品日
            sold_date,                    # U: 売却日
            master.get("shiire", ""),     # V: 仕入れ値
            master.get("shiire_date", ""),# W: 仕入れ日
        ])

    yield f"合計 {len(all_rows)} 件集計完了"

    # ── Step 6: スプレッドシート書き込み ────────────────────────────────
    try:
        # C: クリア前に前回のカート状況(SKU→○/△/✗)を読む（差分でカート喪失を検知）
        prev_cart = {}
        if out_spreadsheet_id:
            try:
                _old = svc.spreadsheets().values().get(
                    spreadsheetId=out_spreadsheet_id, range="商品一覧!A2:H5000",
                ).execute().get("values", [])
                for _r in _old:
                    if _r and len(_r) > _SCOL_CART and _r[_SCOL_SKU].strip():
                        prev_cart[_r[_SCOL_SKU].strip()] = _r[_SCOL_CART].strip()
            except Exception:
                pass

        if out_spreadsheet_id:
            ss_id  = out_spreadsheet_id
            ss_url = f"https://docs.google.com/spreadsheets/d/{ss_id}/edit"
            yield "既存スプレッドシートをクリアして上書きします..."
            # シートが存在しない場合は先に作成する
            try:
                svc.spreadsheets().values().clear(
                    spreadsheetId=ss_id, range="商品一覧!A1:T5000",
                ).execute()
            except Exception:
                yield "  → 「商品一覧」シートが未作成のため新規追加します..."
                svc.spreadsheets().batchUpdate(
                    spreadsheetId=ss_id,
                    body={"requests": [{"addSheet": {"properties": {"title": "商品一覧"}}}]},
                ).execute()
        else:
            yield "新規スプレッドシートを作成中..."
            ss = svc.spreadsheets().create(body={
                "properties": {"title": "Amazon商品サマリー"},
                "sheets": [{"properties": {"title": "商品一覧"}}],
            }).execute()
            ss_id  = ss["spreadsheetId"]
            ss_url = ss["spreadsheetUrl"]
            yield f"作成完了: {ss_url}"

        yield f"データ書き込み中... ({len(all_rows)} 件)"
        svc.spreadsheets().values().update(
            spreadsheetId=ss_id, range="商品一覧!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [_SUMMARY_HEADERS] + all_rows},
        ).execute()
        yield "  → 書き込み完了"

        # 書式: 1行固定・行高120px・写真列160px
        sheet_id = svc.spreadsheets().get(spreadsheetId=ss_id).execute()["sheets"][0]["properties"]["sheetId"]
        svc.spreadsheets().batchUpdate(
            spreadsheetId=ss_id,
            body={"requests": [
                {"updateSheetProperties": {
                    "properties": {"sheetId": sheet_id,
                                   "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }},
                {"updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "ROWS",
                              "startIndex": 1, "endIndex": max(len(all_rows), 1) + 1},
                    "properties": {"pixelSize": 120}, "fields": "pixelSize",
                }},
                {"updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                              "startIndex": 3, "endIndex": 4},  # 写真列
                    "properties": {"pixelSize": 160}, "fields": "pixelSize",
                }},
            ]},
        ).execute()

        yield f"完了！ {len(all_rows)} 件を書き込みました"
        yield f"URL: {ss_url}"

        # ── C/D: カート喪失検知 → Discord通知 ＋ 履歴追記（失敗しても本処理は止めない）──
        try:
            won = partial = lost = 0
            losses = []
            for row in all_rows:
                bb = row[_SCOL_CART]
                if bb == "○":
                    won += 1
                elif bb == "△":
                    partial += 1
                elif bb == "✗":
                    lost += 1
                else:
                    continue
                # 前回○ → 今回○でない＝カート喪失
                if prev_cart.get(row[_SCOL_SKU]) == "○" and bb != "○":
                    losses.append(row)

            if losses:
                lines = [f"⚠️ **カート喪失アラート**（{len(losses)} 件）"]
                for row in losses[:20]:
                    nm = str(row[_SCOL_NAME])[:30]
                    lines.append(
                        f"・{nm}  販売¥{row[_SCOL_HANBAI]} / カート¥{row[_SCOL_CART_PRICE]}  (SKU {row[_SCOL_SKU]})")
                if len(losses) > 20:
                    lines.append(f"…ほか {len(losses) - 20} 件")
                if _discord_webhook_url():
                    sent = _post_discord("\n".join(lines))
                    note = "（Discord通知済み）" if sent else "（Discord送信失敗）"
                else:
                    note = "（Discord未設定のため通知スキップ）"
                yield f"🔔 カート喪失 {len(losses)} 件{note}"

            # D: カート履歴に1行追記（獲得率の推移用）
            total_cart = won + partial + lost
            rate = round(won / total_cart * 100, 1) if total_cart else 0.0
            now_jst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
            _append_cart_history(svc, ss_id, [now_jst, total_cart, won, partial, lost, rate])
        except Exception:
            pass
    except Exception as e:
        import traceback
        yield f"❌ 書き込みエラー: {e}"
        yield traceback.format_exc()
