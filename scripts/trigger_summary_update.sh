#!/usr/bin/env bash
# 商品サマリー自動更新ワークフローを外部から手動トリガーするスクリプト。
# cron-job.org 等の外部cronが叩くのと同じ GitHub REST API を呼ぶ。
# 動作確認用 兼 ドキュメント。
#
# 使い方:
#   GITHUB_PAT=ghp_xxxx ./scripts/trigger_summary_update.sh
#
# PAT は「Fine-grained token / Repository: streamlit-web-ops / Actions: Read and write」で発行する。

set -euo pipefail

: "${GITHUB_PAT:?環境変数 GITHUB_PAT にトークンを設定してください}"

OWNER="kotasaitotrade"
REPO="streamlit-web-ops"
WORKFLOW_ID="297993789"   # update-summary.yml

HTTP_CODE=$(curl -sS -o /tmp/_dispatch_resp -w "%{http_code}" \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GITHUB_PAT}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW_ID}/dispatches" \
  -d '{"ref":"main"}')

if [ "$HTTP_CODE" = "204" ]; then
  echo "✅ トリガー成功 (HTTP 204)。Actions タブで実行を確認できます。"
else
  echo "❌ 失敗 (HTTP ${HTTP_CODE})"
  cat /tmp/_dispatch_resp
  exit 1
fi
