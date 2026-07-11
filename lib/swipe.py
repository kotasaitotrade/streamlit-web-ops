"""スワイプ仕分け用の双方向カスタムコンポーネント宣言。

declare_component はページスクリプト(exec実行)直下だと _get_module_name で失敗するため、
通常importされるこのモジュールで宣言する。
"""
from __future__ import annotations

import os

import streamlit.components.v1 as components

_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "components", "swipe_cards")
_swipe = components.declare_component("swipe_cards", path=_DIR)


def swipe_cards(cards, key=None, default=None):
    """cards=[{id,cat,text,img?}] を表示（img=画像URLがあれば表示）。
    本文はカード内の「✏️編集」でその場修正できる。
    保存時に {decisions:{id:'a'|'s'}, edits:{id:編集後本文}, nonce} を返す。"""
    return _swipe(cards=cards, key=key, default=default)
