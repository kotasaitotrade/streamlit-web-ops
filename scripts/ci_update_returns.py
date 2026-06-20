"""GitHub Actions 用: 返品管理シート（別タブ「返品管理」）を更新する。

認証情報は環境変数（GitHub Secrets）から読み込む。コードにハードコードしない。

必要な環境変数:
  GSPREAD_TOKEN_JSON   : gspread_token.json の中身
  SP_LWA_APP_ID / SP_LWA_CLIENT_SECRET / SP_REFRESH_TOKEN / SP_SELLER_ID / SP_MARKETPLACE_ID
任意:
  RETURNS_DAYS         : 取得対象期間（日）。未指定なら 90。
"""
import os
import sys
import types

SUMMARY_SS_ID = "1TEp7CTkDtApX8agWufw7v9w9hYkif58MLJcKwjz4mic"


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"❌ 環境変数 {name} が未設定です。GitHub Secrets を確認してください。")
        sys.exit(1)
    return val


def main() -> int:
    token_json = _require("GSPREAD_TOKEN_JSON")
    with open("gspread_token.json", "w") as f:
        f.write(token_json)

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

    st_mod = types.ModuleType("streamlit")
    st_mod.secrets = _Secrets()
    st_mod.cache_resource = lambda f: f
    st_mod.cache_data = lambda f: f
    sys.modules["streamlit"] = st_mod

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import lib.amazon_api as amazon

    days = int(os.environ.get("RETURNS_DAYS", "90"))
    print(f"=== 返品管理シート更新 → {SUMMARY_SS_ID}（過去{days}日）===", flush=True)
    try:
        for msg in amazon.run_create_returns_sheet(SUMMARY_SS_ID, days=days):
            print(msg, flush=True)
    except Exception as e:
        import traceback
        print(f"\n❌ エラー: {e}")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
