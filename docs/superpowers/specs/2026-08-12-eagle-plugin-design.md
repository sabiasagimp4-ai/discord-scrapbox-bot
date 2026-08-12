# Eagle Plugin版 Scrapbox動画取り込み 設計書

## 目的

現在の `eagle_bridge.py` に分離されているWindows側の取り込み処理を、Eagle内から起動・確認できる公式Eagleプラグインへ移行する。Discordの `/eagle import-all` 操作も残し、プラグインとDiscordのどちらからでも同じジョブキューを利用できるようにする。

## 採用方針

Eagleプラグインを、次の責務を持つローカルワーカー兼UIとして実装する。

- Botの認証済みAPIからScrapbox全ページの動画プレビューを取得する
- ユーザー確認後にBotへ取り込みジョブを作成する
- ジョブをclaimし、ローカルの `yt-dlp` と `ffmpeg` で動画をダウンロードする
- `eagle.item.addFromPath()` でEagleへ登録する
- タイトル、元URL、ScrapboxページURL、元行、タグをメタデータに保存する
- 成否をBotへ報告し、画面に進捗を表示する

Botのジョブ状態と既存のDiscord DM通知は維持する。`eagle_bridge.py` は後方互換のCLIとして残す。

## API

既存の `EAGLE_BRIDGE_TOKEN` をプラグインAPIにも使用する。

- `POST /eagle/preview`: 全ページを走査してプレビューを作成する
- `POST /eagle/confirm`: 指定プレビューをpendingジョブへ変換する
- `GET /eagle/status`: 現在のジョブ件数を返す
- `GET /eagle/jobs?limit=N`: pendingジョブをclaimする
- `POST /eagle/jobs/{job_id}/result`: プラグインの成否を反映する

全APIは `X-Eagle-Bridge-Token` ヘッダーで認証する。preview/confirm/statusを追加しても、既存のDiscordコマンドの動作は変更しない。

## Eagleプラグイン

`eagle-plugin/` に独立したインストール可能なプラグインを作る。

- `manifest.json`: Windows向けウィンドウプラグインの定義
- `index.html`: プレビュー、確認、進捗、設定画面
- `plugin.js`: Eagle API、Bot API、yt-dlp実行、ジョブ処理
- `README.md`: インストール、`yt-dlp`/`ffmpeg`、Bot URLとトークン設定

Node.jsの `child_process.spawn()` で `yt-dlp` を実行し、EagleプラグインのNode.js Native APIと `eagle.item.addFromPath()` を利用する。ダウンロード先はOSの一時ディレクトリとし、Eagle登録後に削除する。既存manifestをプラグイン側にも保存し、同じURLを二重登録しない。

## エラー処理

- プレビュー取得失敗は画面に表示し、ジョブ作成しない
- 個別動画の失敗はジョブ単位でBotへ報告し、他のジョブは継続する
- `yt-dlp`、`ffmpeg`、Eagle未起動、ネットワーク失敗はエラー内容を最大500文字に制限して表示する
- Bot再起動でジョブが失われる既存制約はREADMEに明記する
- 同じジョブ結果を再送してもBot側は冪等に処理する

## テスト

- Python: preview/confirm/status APIの認証、応答形式、既存ジョブAPIとの互換性
- JavaScript: URL正規化、manifestの重複排除、yt-dlp引数生成、APIクライアントのエラー処理
- 実機Eagle、実動画、実Scrapboxを使うE2Eは環境依存のため未実施と明記する

