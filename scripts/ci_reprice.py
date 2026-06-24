"""GitHub Actions 用: カート価格5%自動調整を実行する。

必要な環境変数（ci_update_summary.py と同じ Secrets）:
  GSPREAD_TOKEN_JSON   : gspread_token.json の中身
  SP_LWA_APP_ID        : Amazon SP-API LWA App ID
  SP_LWA_CLIENT_SECRET : Amazon SP-API LWA Client Secret
  SP_REFRESH_TOKEN     : Amazon SP-API リフレッシュトークン
  SP_SELLER_ID         : セラーID
  SP_MARKETPLACE_ID    : マーケットプレイスID
"""
import os
import sys
import types


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

    try:
        for msg in amazon.run_auto_reprice():
            print(msg, flush=True)
    except Exception as e:
        import traceback
        print(f"\n❌ エラー: {e}")
        traceback.print_exc()
        return 1

    print("\n✅ 自動調整完了", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
