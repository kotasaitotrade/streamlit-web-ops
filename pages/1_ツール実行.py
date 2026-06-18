"""ツール実行ページ。allowed_tools のみ表示し、選択 → 実行 → jobs に enqueue。"""
import json
import streamlit as st

from lib.sheets import (
    materialize_secrets, list_tools, user_allowed_tools,
    enqueue_job, get_tool,
)
from lib.auth import require_login, logout_button

materialize_secrets()

user = require_login()
logout_button()

st.title("🚀 ツール実行")

all_tools = list_tools()
all_tool_names = [t["tool_name"] for t in all_tools]
allowed = user_allowed_tools(user, all_tool_names)
my_tools = [t for t in all_tools if t["tool_name"] in allowed]

if not my_tools:
    st.info("実行可能なツールがありません。")
    st.stop()

labels = [f"{t.get('display_name') or t['tool_name']}（{t['tool_name']}）" for t in my_tools]
idx = st.selectbox("ツールを選択", range(len(my_tools)), format_func=lambda i: labels[i])
tool = my_tools[idx]

st.markdown(f"### {tool.get('display_name') or tool['tool_name']}")
if tool.get("description"):
    st.write(tool["description"])

# パラメータ入力（JSON Schema があれば後で自動生成）
schema_raw = tool.get("params_schema_json") or ""
params: dict = {}
if schema_raw and schema_raw.strip() not in ("", "{}"):
    try:
        schema = json.loads(schema_raw)
        st.caption("パラメータ")
        for key, spec in schema.get("properties", {}).items():
            label = spec.get("title") or key
            default = spec.get("default", "")
            if spec.get("enum"):
                params[key] = st.selectbox(label, spec["enum"], index=spec["enum"].index(default) if default in spec["enum"] else 0)
            elif spec.get("type") == "integer":
                params[key] = st.number_input(label, value=int(default or 0), step=1)
            elif spec.get("type") == "boolean":
                params[key] = st.checkbox(label, value=bool(default))
            else:
                params[key] = st.text_input(label, value=str(default))
    except Exception as e:
        st.warning(f"params_schema_json のパースに失敗: {e}")

st.divider()
if st.button("▶ 実行をリクエスト", type="primary"):
    try:
        job_id = enqueue_job(
            user_email=user["email"],
            tool_name=tool["tool_name"],
            params=params,
            worker_host=tool.get("worker_host", ""),
        )
        st.success(f"ジョブを受け付けました: {job_id}")
        st.caption("実行履歴ページでステータスを確認できます。")
        st.page_link("pages/2_実行履歴.py", label="📋 実行履歴へ")
    except Exception as e:
        st.error(f"受付に失敗: {e}")
