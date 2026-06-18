# 商品サマリー 毎時自動更新（外部cron方式）

GitHub Actions の `schedule:` トリガーは発火が不安定なため、外部の無料cron
（cron-job.org）から `workflow_dispatch` API を毎時叩いて確実に動かす。

## 仕組み

```
cron-job.org（毎時）──HTTPS POST──▶ GitHub REST API ──▶ 商品サマリー自動更新 workflow
```

Mac の起動状態に依存せず、クラウドだけで毎時更新が回る。

## セットアップ手順（初回のみ・約5分）

### ① GitHub トークン（PAT）を発行
1. https://github.com/settings/personal-access-tokens/new を開く
2. 設定:
   - **Token name**: `summary-cron`
   - **Expiration**: 任意（無期限にしたい場合は「No expiration」）
   - **Repository access**: Only select repositories → **streamlit-web-ops**
   - **Permissions** → Repository permissions → **Actions** を **Read and write** に
3. 「Generate token」→ 表示された `github_pat_...` をコピー（再表示されないので注意）

### ② cron-job.org にジョブを作成
1. https://cron-job.org にアクセスし無料アカウント作成 → ログイン
2. 「CREATE CRONJOB」で以下を設定:

   | 項目 | 値 |
   |---|---|
   | Title | 商品サマリー更新 |
   | URL | `https://api.github.com/repos/kotasaitotrade/streamlit-web-ops/actions/workflows/297993789/dispatches` |
   | Schedule | Every hour（毎時。分は :17 等、:00 を避ける） |

3. 「ADVANCED」→ Request method を **POST**、Request body に:
   ```json
   {"ref":"main"}
   ```
4. **Headers** に以下2つを追加:
   - `Authorization` : `Bearer github_pat_...`（①のトークン）
   - `Accept` : `application/vnd.github+json`
5. 保存。「TEST RUN」で **HTTP 204** が返ればOK（Actionsタブに実行が出る）。

## ローカルでの動作確認

```bash
GITHUB_PAT=github_pat_xxxx ./scripts/trigger_summary_update.sh
# → ✅ トリガー成功 (HTTP 204)
```

## メモ
- 既存の GitHub `schedule:` トリガーは残してある（いつか発火しても二重起動は
  workflow 側の concurrency で防止）。不要なら update-summary.yml の schedule 行を消す。
- 更新頻度を変えたい場合は cron-job.org のスケジュールを変更するだけ。
