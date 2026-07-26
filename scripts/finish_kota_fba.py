"""
kota FBA 仕上げスクリプト（FNSKU付与待ち後の再実行用）

出品登録は完了済みだが、Amazon の FNSKU 付与に時間がかかるため、
FNSKU が付いた頃に本スクリプトを再実行して
「FNSKUラベルPDF生成 → FBA納品プラン作成 → 3.発送待ちへ更新」を完了させる。

run_fba_inbound は put_listings_item が冪等なので、何度実行しても安全。
FNSKU が全件揃うまで繰り返し実行してよい。

実行:
  cd /Users/user/git/streamlit-web-ops
  python3 scripts/finish_kota_fba.py
"""
import sys, os, warnings
warnings.filterwarnings('ignore')

# streamlit-web-ops ルートで実行する前提（st.secrets が .streamlit/secrets.toml を読む）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import lib.amazon_api as amazon

KOTA_SS = '1SKRIPf38mv3ZzYnA4hFAmSVk6H5qYrzunfmFCGuqk0M'
OUT_PDF = os.path.expanduser('~/Desktop/fnsku_labels_kota_latest.pdf')


def main():
    logs, pdf, plan = amazon.run_fba_inbound(
        account_name='kota',
        dry_run=False,
        spreadsheet_id=KOTA_SS,
        label_layout='24面 (3×8)',
    )
    for l in logs:
        print(l)

    # FNSKU 取得件数をログから判定
    fnsku_line = next((l for l in logs if 'FNSKU取得:' in l), '')
    got = 0
    if 'FNSKU取得:' in fnsku_line:
        try:
            got = int(fnsku_line.split('FNSKU取得:')[1].split('件')[0].strip())
        except Exception:
            got = 0

    if pdf and got > 0:
        with open(OUT_PDF, 'wb') as f:
            f.write(pdf)
        print(f'\n✅ FNSKUラベルPDF保存: {OUT_PDF} (FNSKU {got}件)')
        print(f'納品プラン: {plan}')
    else:
        print(f'\n⏳ FNSKU未付与（{got}件）。まだ完了できません。時間をおいて再実行してください。')
    return got


if __name__ == '__main__':
    sys.exit(0 if main() > 0 else 2)
