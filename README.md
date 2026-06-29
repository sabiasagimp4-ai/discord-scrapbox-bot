# discord-scrapbox-bot

Discordの特定チャンネルで特定キーワードを含むメッセージを監視し、URLを自動でScrapboxに保存するBot。

---

## 動作の流れ

### 自動保存（メッセージ監視）

1. 指定チャンネルに `{キーワード} {URL}` を含むメッセージを送信
2. BotがURLを検出し、タイトルを取得
3. ScrapboxにページをImport API経由で作成
4. DiscordにScrapboxページのURLをリプライ（複数URLが含まれる場合は1件ずつではなく1通にまとめて返信。サムネイルがある場合はEmbedで表示）

```
[ユーザー] 保存 https://youtu.be/xxxxxx
[Bot]      保存しました https://scrapbox.io/myproject/動画タイトル
```

### `/save` スラッシュコマンド

指定チャンネルで `/save url:https://youtu.be/xxxxxx` を実行すると、キーワード不要で同じ保存処理が走ります。実行者名とURLに続けて、タイトル・サムネイル・Scrapboxページへのリンクを含むEmbedが返信されます。

`overwrite:true` を指定すると、同タイトルのページが既に存在していても上書き保存します（通常は既存ページがあれば新規作成をスキップします）。

### YouTubeプレイリストの一括保存

URLに `list=` パラメータ（再生リストID）が含まれる場合、`YOUTUBE_API_KEY` 設定時は再生リスト内の動画を自動展開し、1件ずつ通常の保存処理を実行します（自動保存・`/save` 両方対応）。

- `YOUTUBE_API_KEY` 未設定時は展開せず、URLをそのまま1件として処理します
- 1回の展開につき最大50件まで取得します
- 再生リスト内の重複動画、および同一メッセージ内の重複URLは除外されます（個別の重複保存防止は既存ページ判定で行われます）
- DiscordのEmbedは1メッセージ最大10件のため、保存件数が10件を超える場合は先頭10件のみ表示されます

### `/alias` スラッシュコマンド

人物名の表記ゆれ（`CREDIT_MAPPING_PAGE` で指定したScrapboxページ）を管理するコマンドです。

| サブコマンド | 説明 | 権限 |
|---|---|---|
| `/alias add canonical:山田太郎 alias:タロー` | 別名を追加します | サーバーの管理 |
| `/alias remove canonical:山田太郎 alias:タロー` | 別名を削除します（別名が0件になった本名の行は削除されます） | サーバーの管理 |
| `/alias list` | 登録済みの表記ゆれ一覧を表示します | 不要 |

- いずれも `CREDIT_MAPPING_PAGE` が未設定の場合は実行できません
- `add`/`remove` は既存の表記ゆれをむやみに上書きしません（`add` は同じ別名が登録済みなら何もせず通知、`remove` は未登録の本名・別名を指定するとエラーを返します）

### `/status` スラッシュコマンド

Bot本体とBotが依存する各サービスへの疎通状況を確認するコマンドです。指定チャンネルで `/status` を実行すると、以下の項目をまとめて返信します。

| 項目 | 確認内容 |
|---|---|
| Discord | Botが応答中であることの確認（常に✅） |
| Scrapbox | `SCRAPBOX_SID` Cookieでページ一覧を取得できるか |
| YouTube Data API | `YOUTUBE_API_KEY` の有効性 |
| OpenRouter(AI) | `OPENROUTER_API_KEY` の有効性 |
| Gyazo | `GYAZO_ACCESS_TOKEN` の有効性 |

各項目は ✅（正常） / ❌（異常） / ⏭️（未設定のためスキップ）のいずれかで表示されます。`YOUTUBE_API_KEY`・`OPENROUTER_API_KEY`・`GYAZO_ACCESS_TOKEN` は任意設定のため、未設定でも❌にはならず⏭️として扱われます。実行に特別な権限は不要です。

### `/debug` スラッシュコマンド

指定したURLについて、Botが実際に取得するタイトル・概要欄・取得元（YouTube Data API / oEmbed / Vimeo oEmbed / HTML `<title>` のいずれを使ったか）、および取得した概要欄からOpenRouterで抽出されるクレジット情報を確認できます。Scrapboxへの保存は行いません。

- YouTubeの概要欄がクレジット抽出されない場合、`YOUTUBE_API_KEY` が正しく概要欄を取得できているか（取得元が「YouTube Data API」になっているか）を確認するのに使えます。取得元が「YouTube oEmbed」になっている場合は、APIキーが未設定または無効なため概要欄が取得できていないことを示します。
- 概要欄は取得できているのにクレジットが保存されない場合は、「クレジット抽出結果(OpenRouter)」を確認してください。`(OPENROUTER_API_KEY未設定のためスキップ)` ならキー自体が未設定、`(抽出結果なし)` ならOpenRouterへの問い合わせ自体は行われたが該当情報が見つからなかった（プロンプトやモデルの精度の問題の可能性）ことを示します。

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
├── bot.py                      # メインロジック
├── credit_extractor.py         # LLMによるクレジット抽出
├── name_linker.py               # Scrapbox人物名リンク照合
├── gyazo_uploader.py            # サムネイル画像のGyazoアップロード
├── playlist_loader.py           # YouTube再生リストの動画URL展開
├── tests/                       # 単体テスト（unittest）
├── .github/workflows/test.yml   # CI（push/PR時に単体テストを自動実行）
├── requirements.txt             # 依存パッケージ（discord.py, requests）
├── Dockerfile                    # コンテナ定義
└── fly.toml                      # 未使用（Fly.io用、Renderでは不要）
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

`master` への push・PR作成時にGitHub Actions（`.github/workflows/test.yml`）で自動実行されます。
