# discord-scrapbox-bot

Discordの特定チャンネルで特定キーワードを含むメッセージを監視し、URLを自動でScrapboxに保存するBot。

---

## 動作の流れ

1. 指定チャンネルに `{キーワード} {URL}` を含むメッセージを送信
2. BotがURLを検出し、タイトルを取得
3. ScrapboxにページをImport API経由で作成
4. DiscordにScrapboxページのURLをリプライ

```
[ユーザー] 保存 https://youtu.be/xxxxxx
[Bot]      保存しました https://scrapbox.io/myproject/動画タイトル
```

---

## タイトル取得ロジック

| URL種別 | 取得方法 |
|---|---|
| YouTube | oEmbed API（認証不要・確実） |
| Vimeo | oEmbed API（認証不要・確実） |
| その他 | HTMLの `<title>` タグをスクレイピング |

---

## セットアップ

### 1. Discord Botを作る

1. https://discord.com/developers/applications → New Application
2. 左メニュー「Bot」→「Add Bot」
3. **Message Content Intent** をON
4. トークンをコピー
5. OAuth2 → URL Generator → Scopes: `bot`、Permissions: `Read Messages` + `Send Messages` → URLでサーバーに招待

チャンネルIDはDiscordの設定 → 詳細設定 → **開発者モード** をONにして、チャンネルを右クリック → **IDをコピー** で取得。

### 2. Render.comにデプロイ

1. https://render.com でGitHubアカウントでサインアップ
2. New + → **Web Service**
3. このリポジトリを選択（Runtime: Docker、Plan: Free）
4. 環境変数を設定（下記参照）
5. Deploy

### 3. UptimeRobotでスリープ防止（必須）

Renderの無料Web Serviceは15分アクセスがないとスリープします。

1. https://uptimerobot.com で登録
2. New Monitor → HTTP(S)
3. URL: `https://{your-app-name}.onrender.com`
4. Interval: 5 minutes

---

## 環境変数

Render → Environment から設定します。

| 変数名 | 必須 | 説明 | 例 |
|--------|------|------|----|
| `DISCORD_TOKEN` | ✅ | BotのTokens | `MTI3...` |
| `CHANNEL_ID` | ✅ | 監視するチャンネルのID（数字） | `1234567890123456789` |
| `SCRAPBOX_PROJECT` | ✅ | ScrapboxのプロジェクトURL名 | `myproject` |
| `SCRAPBOX_SID` | ✅ | Scrapboxの `connect.sid` Cookie値 | `s%3Axxxxxx...` |
| `KEYWORD` | — | 絞り込みキーワード（空で全メッセージ対象） | `保存` |

### connect.sid の取得方法

1. ブラウザでhttps://scrapbox.io にログイン
2. DevTools（F12）→ Application → Cookies → `scrapbox.io`
3. `connect.sid` のValue列をコピー（`s%3A` で始まる文字列）

> Cookieには有効期限があります。保存が403エラーになり始めたら再取得してください。

---

## ファイル構成

```
discord-scrapbox-bot/
├── bot.py           # メインロジック
├── requirements.txt # 依存パッケージ（discord.py, requests）
├── Dockerfile       # コンテナ定義
└── fly.toml         # 未使用（Fly.io用、Renderでは不要）
```

---

## Scrapboxページ形式

```
動画タイトル
[https://youtu.be/xxxxxx]
```

YouTube・Vimeoは動画プレイヤーとして埋め込まれます。

---

## ローカル実行

```bash
pip install -r requirements.txt

export DISCORD_TOKEN=...
export CHANNEL_ID=...
export SCRAPBOX_PROJECT=...
export SCRAPBOX_SID=...
export KEYWORD=保存

python bot.py
```
