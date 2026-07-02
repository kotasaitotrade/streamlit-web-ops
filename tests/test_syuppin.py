"""lib/syuppin.py のロジックテスト（ネットワーク不要）。

実行: pytest tests/test_syuppin.py
UI（Streamlit）と Sheets 書き込みは実機・実データで別途検証済み。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lib.syuppin as sp  # noqa: E402


@pytest.mark.parametrize("idx,expected", [
    (0, "A"), (25, "Z"), (26, "AA"), (30, "AE"), (51, "AZ"), (52, "BA"),
])
def test_col_to_a1(idx, expected):
    assert sp.col_to_a1(idx) == expected


def test_build_dataframe_pads_and_rownum():
    headers = ["管理ID", "ステータス", "備考"]
    df = sp.build_dataframe(headers, [["RS001", "1.1.発注済み"], ["RS002", "2.写真撮影済み", "m"]])
    assert list(df.columns) == ["__row", "管理ID", "ステータス", "備考"]
    assert df.iloc[0]["__row"] == 2 and df.iloc[1]["__row"] == 3
    assert df.iloc[0]["備考"] == ""


def test_today_jst_format():
    s = sp.today_jst()
    assert len(s) == 10 and s.count("/") == 2


def test_resolve_worker_key_from_column():
    assert sp.resolve_worker_key({"worker_key": "kaho"}) == "kaho"


def test_resolve_worker_key_from_display_name():
    assert sp.resolve_worker_key({"display_name": "矢野加帆"}) == "kaho"


def test_resolve_worker_key_none_when_unknown():
    assert sp.resolve_worker_key({"display_name": "知らない人"}) is None


def test_is_manager():
    assert sp.is_manager({"role": "admin"})
    assert not sp.is_manager({"role": "operator"})


HEADERS = ["管理ID", "ステータス", "販売価格", "状態", "撮影日", "備考"]
DISP = ["管理ID", "状態", "販売価格", "ステータス", "備考"]
CI = {h: i for i, h in enumerate(HEADERS)}


def _run(orig, edited, rows, shoot):
    return sp.diff_updates(HEADERS, orig, edited, rows, shoot)


def test_no_change_no_update():
    o = pd.DataFrame([["RS001", "良い", "1000", "2.写真撮影済み", "x"]], columns=DISP)
    ups, n = _run(o, o.copy(), [2], ["2026/06/01"])
    assert ups == [] and n == 0


def test_status_to_shot_autofills_photo_date():
    o = pd.DataFrame([["RS001", None, None, "1.1.発注済み", "m"]], columns=DISP)
    e = o.copy(); e.loc[0, "ステータス"] = sp.DONE_SHOOT_STATUS
    ups, _ = _run(o, e, [2], [""])
    assert (2, CI["ステータス"], sp.DONE_SHOOT_STATUS) in ups
    assert (2, CI["撮影日"], sp.today_jst()) in ups


def test_status_to_shot_does_not_overwrite_existing_date():
    o = pd.DataFrame([["RS001", "良い", "1000", "1.1.発注済み", "m"]], columns=DISP)
    e = o.copy(); e.loc[0, "ステータス"] = sp.DONE_SHOOT_STATUS
    ups, _ = _run(o, e, [2], ["2026/06/01"])
    assert not any(u[1] == CI["撮影日"] for u in ups)


def test_clearing_selectbox_writes_empty_not_none():
    o = pd.DataFrame([["RS001", "良い", "1000", "2.写真撮影済み", "m"]], columns=DISP)
    e = o.copy(); e.loc[0, "状態"] = None
    ups, _ = _run(o, e, [2], ["2026/06/01"])
    assert (2, CI["状態"], "") in ups
    assert not any(str(u[2]).lower() == "none" for u in ups)


def test_only_changed_cells_updated():
    o = pd.DataFrame([
        ["RS001", "良い", "1000", "1.1.発注済み", "m"],
        ["RS002", "可", "2000", "1.1.発注済み", "n"],
    ], columns=DISP)
    e = o.copy(); e.loc[0, "販売価格"] = "1500"
    ups, n = _run(o, e, [2, 3], ["", ""])
    assert (2, CI["販売価格"], "1500") in ups
    assert all(u[0] == 2 for u in ups) and n == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
