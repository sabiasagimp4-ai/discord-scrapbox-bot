# クレジット表記機能 設計書（フェーズ1: YouTube / Vimeo）

## 1. 背景・目的

元になった Chrome 拡張機能 [youtube-to-scrapbox](https://github.com/sabiasagimp4-ai/youtube-to-scrapbox) は、動画ページをブラウザで開いた状態でコンソール経由でDOM/説明欄を取得し、LLMでクレジットを抽出してScrapboxに保存していた。

現行の `discord-scrapbox-bot` はDiscord上のURLをサーバーサイドで受け取って保存するだけで、ブラウザコンテキストを持たないため、クレジット抽出機能が未実装。

本書は、**ブラウザ操作なし・公開API/サーバーサイド処理のみで再現可能な範囲**に絞った設計を定義する。

## 2. スコープ

### 対象
- YouTube
- Vimeo

### 対象外（将来フェーズ）
- Instagram / Twitter(X)：ログインセッションが必要でJS-SPAのため安定したサーバーサイド取得が困難
- ニコニコ動画：非公式APIのみで仕様変更に弱い
- ブラウザコンソール相当の動的取得（"もっと見る"展開後のテキスト等）：ヘッドレスブラウザ導入が必要になり構成が大きく変わるため別検討

## 3. 処理フロー

```
Discord message (URL)
      │
      ▼
[1] メタデータ取得
      title / channelTitle / description を取得
      │
      ▼
[2] LLMクレジット抽出
      description → 構造化クレジット (役職: 名前 のリスト)
      │
      ▼
[3] Scrapbox人物名リンク照合
      抽出した各名前を既存ページと照合 → [名前] 形式に変換
      │
      ▼
[4] 重複チェック
      同タイトルページの既存有無を確認
      │
      ▼
[5] Scrapboxページ作成（Import API）
      │
      ▼
[6] Discordにリプライ
```

## 4. コンポーネント設計

### 4.1 メタデータ取得

現在の `fetch_title()` を `fetch_metadata()` に拡張し、`title` に加えて `description` を返すようにする。

| サービス | API | 取得項目 |
|---|---|---|
| YouTube | YouTube Data API v3 `videos.list?part=snippet&id={videoId}&key={API_KEY}` | `title`, `description`, `channelTitle` |
| Vimeo | Vimeo oEmbed `https://vimeo.com/api/oembed.json?url={url}`（既存と同じエンドポイント） | `title`, `description`, `author_name` |

YouTubeはoEmbedにdescriptionが含まれないため、Data API v3に切り替える。APIキーは無料枠（1日10,000ユニット、`videos.list`は1リクエスト1ユニット）で十分。

新規環境変数: `YOUTUBE_API_KEY`

### 4.2 LLMクレジット抽出

`description` テキストをLLMに渡し、構造化クレジットを抽出する。

**入力例:**
```
監督: 山田太郎
イラスト: 鈴木花子 (@hanako_art)
... (その他の説明文)
```

**プロンプト方針:**
- 役職名と人物名のペアを抽出させる（役職表記の揺れ「Direction/監督/演出」は正規化せずそのまま出力でよい。正規化は照合ロジック側で吸収）
- 該当情報が無い場合は空配列を返させる
- 出力は JSON 固定フォーマットで受け取り、パースエラー時はクレジット無しとして処理を継続（保存自体は失敗させない）

**出力フォーマット:**
```json
{"credits": [{"role": "Direction", "name": "山田太郎"}, {"role": "Illustration", "name": "鈴木花子"}]}
```

**LLM呼び出し先:** OpenRouter Chat Completions API（`https://openrouter.ai/api/v1/chat/completions`）。既存の `requests` 依存のみで実装し、新規SDKは追加しない。
新規環境変数: `OPENROUTER_API_KEY`、`OPENROUTER_MODEL`（デフォルト: `openai/gpt-oss-120b:free`）

クレジットが0件の場合は本処理をスキップし、従来通りタイトル+URLのみのページを作成する（後方互換）。

### 4.3 Scrapbox人物名リンク照合

元拡張のロジックをそのまま移植する。

**照合順序:**
1. 完全一致（大文字小文字無視）: 既存ページ一覧 (`GET /api/pages/:project?limit=1000`) のタイトルと比較
2. 名前マッピングページ: `表記ゆれ` 等の固定ページに `本名 == 別名1, 別名2` 形式で記載されたエイリアスを参照
3. Dice係数（文字bigram重複率）≥0.9 のページタイトルを採用

一致したページが見つかった場合、本文中の名前を `[名前]` 形式（Scrapboxリンク記法）に変換する。一致しなければプレーンテキストのまま出力する。

**新規モジュール:** `name_linker.py`
- `load_existing_pages(project) -> list[str]`（Scrapbox APIをキャッシュ、TTL付き）
- `resolve_name(name, pages, alias_map) -> str`（リンク化後の文字列を返す）

### 4.4 重複チェック

ページ作成前に `GET /api/pages/:project/{title}` で既存ページの有無を確認する。既存の場合は新規作成をスキップし、Discordには「既に保存済みです {url}」と返す。

### 4.5 Scrapboxページ本文フォーマット

```
{title}
[{url}]
クレジット
 Direction: [山田太郎]
 Illustration: [鈴木花子]
```

クレジットが無い場合は1〜2行目のみ（現行と同じ）。

## 5. 環境変数（追加分）

| 変数名 | 必須 | 説明 |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTubeクレジット抽出を使う場合のみ | YouTube Data API v3 キー |
| `ANTHROPIC_API_KEY` | クレジット抽出を使う場合のみ | LLM呼び出し用 |
| `CREDIT_MAPPING_PAGE` | 任意 | 名前表記ゆれマッピングページのタイトル（未設定ならエイリアス照合をスキップ） |

いずれも未設定の場合は新機能を無効化し、現行動作（タイトル+URLのみ保存）にフォールバックする。

## 6. エラーハンドリング方針

- メタデータ取得・LLM抽出・名前照合のいずれかが失敗しても、最低限「タイトル+URL」のページ保存は継続する（現行の堅牢性を維持）
- LLM呼び出しタイムアウトは5秒程度に制限し、Discordへの応答遅延を防ぐ

## 7. 影響範囲

- `bot.py`: `fetch_title` → `fetch_metadata` に変更、`save_to_scrapbox` にクレジット処理を追加
- 新規ファイル: `name_linker.py`、`credit_extractor.py`
- `requirements.txt`: 変更なし（既存の `requests` で実装）
- README: 新規環境変数の追記

## 8. 未決事項

- 名前マッピングページのフォーマット（Scrapbox側で運用者が手動管理する想定だが、初期データをどう用意するか）
- サムネイル取得・Gyazoアップロードを本フェーズに含めるか（任意機能として別途実装も可）
