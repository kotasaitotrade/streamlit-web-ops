"""
kota 全出品分の FNSKU ラベルPDFを1つにまとめて生成する。

run_fnsku_labels はステータス「3.出品済み」限定で kota の運用(2.写真撮影済み→3.発送待ち)と
合わないため、SKU+ASIN のある全行を対象に _get_fnsku + _build_labels_pdf で直接生成する。

実行:
  cd /Users/user/git/streamlit-web-ops
  python3 scripts/generate_kota_labels.py
出力: ~/Desktop/fnsku_labels_kota_all.pdf
"""
import sys, os, warnings, time
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import lib.amazon_api as A
from sp_api.api import ListingsItems
from sp_api.base import Marketplaces

KOTA_SS = '1SKRIPf38mv3ZzYnA4hFAmSVk6H5qYrzunfmFCGuqk0M'
OUT = os.path.expanduser('~/Desktop/fnsku_labels_kota_all.pdf')
LAYOUT = '24面 (3×8)'


def main():
    all_rows = A._read_rows("R", KOTA_SS)
    targets = []
    for sheet_row, row in all_rows:
        sku  = A._cell(row, A.COL_SKU)
        asin = A._cell(row, A.COL_ASIN)
        status = A._cell(row, A.COL_STATUS)
        if not sku or not asin:
            continue
        if 'キャンセル' in status:
            continue
        price_str = A._cell(row, A.COL_HANBAI)
        try:
            price = int(float(price_str.replace(",", "").replace("¥", "")))
        except Exception:
            price = 0
        targets.append({
            "sheet_row": sheet_row, "kanri_id": A._cell(row, A.COL_KANRI_ID),
            "sku": sku, "asin": asin, "price": price,
            "state_raw": A._cell(row, A.COL_STATE),
        })

    print(f"対象: {len(targets)} 件")
    api = ListingsItems(credentials=A._sp_creds(), marketplace=Marketplaces.JP)
    items = []
    got = 0
    for t in targets:
        fnsku, item_name = A._get_fnsku(api, t["sku"])
        s = t["state_raw"]
        ct = "used_very_good" if "非常" in s else ("used_acceptable" if "許容" in s else "used_good")
        if fnsku:
            got += 1
        print(f"  [{t['kanri_id']}] {t['sku']} FNSKU={fnsku or '未取得'}")
        items.append({**t, "fnsku": fnsku, "item_name": item_name,
                      "condition_type": ct, "condition_jp": A._CONDITION_JP.get(ct, ct)})
        time.sleep(0.2)

    pdf = A._build_labels_pdf(items, layout=LAYOUT)
    with open(OUT, 'wb') as f:
        f.write(pdf)
    print(f"\n✅ PDF保存: {OUT}  ({len(pdf)} bytes)  FNSKU {got}/{len(items)}件")
    return got, len(items)


if __name__ == '__main__':
    g, n = main()
    sys.exit(0 if g == n else 2)
