# Web Ops

業務委託さんが Web 画面からツール実行をリクエストし、ローカル PC の Worker が実行するシステム。

## 構成

```
[Streamlit Cloud]  →  [Google Sheets jobs]  ←  [ローカル Worker (system_trade)]
```

- フロント: このリポ（Streamlit）
- キュー: スプレッドシート `1xgn0MDZRLQeuvgBXtDNaHyRVID6zZRVXDGXALI4YOjM`
  - `jobs`: ジョブキュー
  - `users`: ユーザー権限マスタ
  - `tools`: ツール定義マスタ
- Worker: `system_trade/src/web_jobs_worker.py`

## セットアップ

### 1. Streamlit Cloud にデプロイ
- このリポを GitHub にプッシュ
- Streamlit Cloud で新規アプリ作成 → このリポを選択
- Secrets に `.streamlit/secrets.toml.example` の内容を埋めて登録

### 2. Google OAuth 設定
- Google Cloud Console で OAuth 2.0 クライアントを作成
  - 種類: Web アプリ
  - 承認済みリダイレクト URI: `https://<your-app>.streamlit.app/oauth2callback`
- 取得した client_id / client_secret を secrets の `[auth.google]` に
- `cookie_secret`: `openssl rand -hex 32` で生成

### 3. gspread 用 credentials
- 既存の `streamlit` リポの secrets と同じものでOK
- `[google_credentials]` と `[gspread_token]` をコピー

### 4. シート初期化
- 初回アプリ起動時に `jobs` / `users` / `tools` シートとヘッダが自動作成される
- `users` シートに admin ユーザーを 1 行追加（手動）:
  ```
  email                | display_name | role  | allowed_tools | active
  oosimahourenn@gmail.com | 自分          | admin | *             | TRUE
  ```
- `tools` シートに最初のツールを追加:
  ```
  tool_name          | display_name           | description | required_role | worker_host | command_template                       | params_schema_json
  add_title_to_draft | メルカリ下書き文字入れ | ...         | operator      | PC3         | python src/add_title_to_draft.py --all | {}
  ```

### 5. Worker をローカル PC で起動
- `system_trade/bat/web_jobs_worker.bat` をタスクスケジューラまたは起動時自動実行に登録
- ホスト名 (`WORKER_HOST`) を環境変数で設定

## 開発

```bash
pip install -r requirements.txt
streamlit run app.py
```

## ディレクトリ

```
streamlit-web-ops/
├── app.py                 # ホーム
├── pages/
│   ├── 1_ツール実行.py
│   └── 2_実行履歴.py
├── lib/
│   ├── sheets.py          # gspread 操作
│   └── auth.py            # st.login + users 照合
├── requirements.txt
└── .streamlit/
    └── secrets.toml.example
```
