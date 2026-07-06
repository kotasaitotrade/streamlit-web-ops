"""lib/x_post の非ネットワーク部分の単体テスト（投稿はしない）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lib.x_post as xp


def test_weighted_len_japanese_counts_double():
    assert xp.weighted_len("abc") == 3
    assert xp.weighted_len("あいう") == 6           # 日本語1文字=2
    assert xp.weighted_len("ab あ") == 2 + 1 + 2    # 'a','b',space=1each + 'あ'=2


def test_resolve_image_empty_returns_blank():
    assert xp.resolve_image("") == ""
    assert xp.resolve_image("   ") == ""
    assert xp.resolve_image("src/x_images/does_not_exist_xyz.png") == ""


def test_resolve_image_finds_local_file(tmp_path):
    f = tmp_path / "card.png"
    f.write_bytes(b"x")
    assert xp.resolve_image(str(f)) == str(f)


def test_creds_fallback_reads_local_json_when_no_secrets():
    # streamlit ランタイム外では st.secrets 参照が失敗→ファイルフォールバックする想定。
    # system_trade の config/x_api.json があれば has_credentials は True。
    p = "/Users/user/git/system_trade/config/x_api.json"
    if os.path.exists(p):
        assert xp.has_credentials() is True
