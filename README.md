# discord-scrapbox-bot

Discordの特定チャンネルで特定キーワードを含むメッセージを監視し、URLを自動でScrapboxに保存するBot。

---

## 動作の流れ

### 自動保存（メッセージ監視）

1. 指定チャンネルに `{キーワード} {URL}` を含むメッセージを送信
2. BotがURLを検出し、タイトルを取得
3. ScrapboxにページをImport API経由で作成
4. DiscordにScrapboxページのURLをリプライ

```
[ユーザー] 保存 https://youtu.be/xxxxxx
[Bot]      保存しました https://scrapbox.io/myproject/動画タイトル
```

### `/save` スラッシュコマンド

指定チャンネルで `/save url:https://youtu.be/xxxxxx` を実行すると、キーワード不要で同じ保存処理が走ります。返信は次の3行形式です。

```
実行者名
https://youtu.be/xxxxxx
https://scrapbox.io/myproject/動画タイトル
```

---

## タイトル取得ロジック

| URL種別 | 取得方法 |
|---|---|
| YouTube | `YOUTUBE_API_KEY` 設定時はYouTube Data API v3（説明欄も取得可）、未設定時はoEmbed API（タイトルのみ） |
| Vimeo | oEmbed API（タイトル・説明欄とも取得可） |
| その他 | HTMLの `<title>` タグ＋ `og:image` メタタグ（サムネイル画像として取得） |

タイトルの改行・連続空白は1スペースに正規化されます（Scrapboxのページタイトルは複数行不可のため）。

### サムネイル画像のGyazoアップロード（任意）

`og:image` で取得したサムネイルは、サイト側のホットリンク制限で直リンク表示できない場合があるため、`GYAZO_ACCESS_TOKEN` を設定すると画像をGyazoにアップロードし直し、その恒久URLをScrapboxページに埋め込みます。未設定時は取得した画像URLをそのまま埋め込みます（アップロード失敗時もフォールバックで直リンクを使用）。

---

## クレジット抽出・人物名リンク機能（任意）

`YOUTUBE_API_KEY` または Vimeo の説明欄から、LLM（OpenRouter経由）でクレジット情報（役職・人物名）を抽出し、Scrapboxページに追記します。`OPENROUTER_API_KEY` が未設定の場合はこの処理をスキップし、タイトル＋URLのみのページを作成します。

抽出した人物名は既存のScrapboxページと照合し、一致すれば `[名前]` 形式でリンク化されます（完全一致 → `CREDIT_MAPPING_PAGE` のエイリアス → 文字列の類似度0.9以上の順）。

同タイトルのページが既に存在する場合は新規作成をスキップし、既存ページのURLを返します。

---

## セットアップ

### 1. Discord Botを作る

1. https://discord.com/developers/applications → New Application
2. 左メニュー「Bot」→「Add Bot」
3. **Message Content Intent** をON
4. トークンをコピー
5. OAuth2 → URL Generator → Scopes: `bot` + `applications.commands`、Permissions: `Read Messages` + `Send Messages` → URLでサーバーに招待（`/save`コマンドを使うには `applications.commands` スコープが必須）

チャンネルIDはDiscordの設定 → 詳細設定 → **開発者モード** をONにして、チャンネルを右クリック → **IDをコピー** で取得。

> スラッシュコマンドはDiscordの仕様上グローバル反映に最大1時間ほどかかります。動作確認を早く行いたい場合は環境変数 `GUILD_ID`（サーバーID）を設定すると、そのサーバーには即時反映されます。

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
| `YOUTUBE_API_KEY` | — | YouTube Data API v3キー。設定するとYouTubeの説明欄を取得しクレジット抽出が有効になる | `AIza...` |
| `OPENROUTER_API_KEY` | — | クレジット抽出用LLM（OpenRouter）のAPIキー。未設定時はクレジット抽出をスキップ | `sk-or-...` |
| `OPENROUTER_MODEL` | — | OpenRouterで使用するモデル名（デフォルト: `openai/gpt-oss-120b:free`） | `google/gemini-flash-1.5` |
| `CREDIT_MAPPING_PAGE` | — | 人物名の表記ゆれを管理するScrapboxページ名。`本名 == 別名1, 別名2` の形式で記載した行を参照する | `表記ゆれ` |
| `GUILD_ID` | — | 設定するとそのサーバーIDに`/save`コマンドを即時反映する（未設定時はグローバル反映で最大1時間程度かかる） | `1234567890123456789` |
| `GYAZO_ACCESS_TOKEN` | — | サムネイル画像をGyazoにアップロードし直すためのアクセストークン。未設定時は取得した画像URLを直接埋め込む | `xxxxxxxx-xxxx-...` |

### connect.sid の取得方法

1. ブラウザでhttps://scrapbox.io にログイン
2. DevTools（F12）→ Application → Cookies → `scrapbox.io`
3. `connect.sid` のValue列をコピー（`s%3A` で始まる文字列）

> Cookieには有効期限があります。保存が403エラーになり始めたら再取得してください。Botも403を検出した場合はDiscordに「Cookieが期限切れの可能性があります」と案内するメッセージを返します。

### GYAZO_ACCESS_TOKEN の取得方法

1. https://gyazo.com/api でアプリを登録
2. 発行されたアクセストークンをコピー

---

## ファイル構成

```
discord-scrapbox-bot/
├── bot.py               # メインロジック
├── credit_extractor.py  # LLMによるクレジット抽出
├── name_linker.py       # Scrapbox人物名リンク照合
├── gyazo_uploader.py    # サムネイル画像のGyazoアップロード
├── tests/                # 単体テスト（unittest）
├── requirements.txt     # 依存パッケージ（discord.py, requests）
├── Dockerfile            # コンテナ定義
└── fly.toml              # 未使用（Fly.io用、Renderでは不要）
```

---

## Scrapboxページ形式

```
動画タイトル
[https://youtu.be/xxxxxx]
クレジット
 Direction: [山田太郎]
 Illustration: [鈴木花子]
```

YouTube・Vimeoは動画プレイヤーとして埋め込まれます。その他のURLは `og:image` から取得したサムネイル画像（`GYAZO_ACCESS_TOKEN` 設定時はGyazoアップロード後のURL）をURL行の直後に埋め込みます。クレジット行はLLMが説明欄から抽出できた場合のみ追加されます。

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

---

## テスト

`name_linker.py`・`credit_extractor.py`・`gyazo_uploader.py` には外部APIをモックした単体テストがあります（標準ライブラリの `unittest` のみ使用、追加の依存パッケージ不要）。

```bash
python -m unittest discover -s tests -v
```
