"""GitHub Actions 用: 商品サマリースプレッドシートを更新する。

認証情報は環境変数（GitHub Secrets）から読み込む。コードにハードコードしない
（このリポジトリは公開のため）。

必要な環境変数:
  GSPREAD_TOKEN_JSON   : gspread_token.json の中身（Google OAuth トークン JSON 一式）
  SP_LWA_APP_ID        : Amazon SP-API LWA App ID
  SP_LWA_CLIENT_SECRET : Amazon SP-API LWA Client Secret
  SP_REFRESH_TOKEN     : Amazon SP-API リフレッシュトークン
  SP_SELLER_ID         : セラーID
  SP_MARKETPLACE_ID    : マーケットプレイスID
"""
import json
import os
import sys
import types

# 更新先スプレッドシート（商品サマリー）
SUMMARY_SS_ID = "1TEp7CTkDtApX8agWufw7v9w9hYkif58MLJcKwjz4mic"


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"❌ 環境変数 {name} が未設定です。GitHub Secrets を確認してください。")
        sys.exit(1)
    return val


def main() -> int:
    # ── Google 認証ファイルを Secret から書き出す ──
    # lib/amazon_api._google_creds() は gspread_token.json を読む
    token_json = _require("GSPREAD_TOKEN_JSON")
    with open("gspread_token.json", "w") as f:
        f.write(token_json)

    # ── SP-API 認証情報（st.secrets["sp_api"] をモック） ──
    sp_api = {
        "lwa_app_id":        _require("SP_LWA_APP_ID"),
        "lwa_client_secret": _require("SP_LWA_CLIENT_SECRET"),
        "refresh_token":     _require("SP_REFRESH_TOKEN"),
        "seller_id":         _require("SP_SELLER_ID"),
        "marketplace_id":    _require("SP_MARKETPLACE_ID"),
    }

    class _Section(dict):
        def __getattr__(self, key):
            return self[key]

    class _Secrets:
        def __init__(self):
            self._data = {"sp_api": _Section(sp_api)}

        def __getitem__(self, key):
            return self._data[key]

        def __contains__(self, key):
            return key in self._data

    # streamlit をモック（amazon_api は import 時に streamlit を要求する）
    st_mod = types.ModuleType("streamlit")
    st_mod.secrets = _Secrets()
    st_mod.cache_resource = lambda f: f
    st_mod.cache_data = lambda f: f
    sys.modules["streamlit"] = st_mod

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import lib.amazon_api as amazon

    print(f"=== Amazon商品サマリー更新 → {SUMMARY_SS_ID} ===", flush=True)
    try:
        for msg in amazon.run_create_summary_sheet(SUMMARY_SS_ID):
            print(msg, flush=True)
    except Exception as e:
        import traceback
        print(f"\n❌ エラー: {e}")
        traceback.print_exc()
        return 1
    print("\n✅ サマリー更新完了", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
