# Scrapbox Video Importer for Eagle

Eagle内からBotのScrapbox動画ジョブを確認し、動画をダウンロードしてEagleへ登録するWindows向けプラグインです。

## 必要なもの

- Eagle 4.x（Plugin API対応版）
- Windows x64
- `yt-dlp.exe` がPATHにあること
- `ffmpeg.exe` がPATHにあること
- 稼働中のDiscord Botと `EAGLE_BRIDGE_TOKEN`

## セットアップ

1. `manifest.json`、`index.html`、`plugin.js`、`package.json`を含むこのフォルダーをEagleのプラグインとしてインストールします。
2. Eagleを再起動し、`Scrapbox Video Importer`を開きます。
3. Bot URLにRenderのサービスURL、トークンにBot側の`EAGLE_BRIDGE_TOKEN`を入力します。値はプラグインのローカル設定へ保存されます。
4. 「全ページをスキャン」→プレビュー確認→「このプレビューを取り込む」→「取り込み開始」の順に操作します。

## 動作

プラグインはBotの認証済みAPIからジョブを1件ずつ取得します。`yt-dlp`で一時フォルダーへMP4を保存し、Eagleの`eagle.item.addFromPath()`で登録した後、一時ファイルを削除します。登録済みの正規化URLはホームフォルダーの`/scrapbox-video-import-manifest.json`に保存され、次回はスキップします。

YouTube API、Cookie、ログインセッションは使用しません。非公開・会員限定・年齢制限・地域制限などの動画は失敗する可能性があります。Bot再起動で未完了のクラウド側キューが失われる制約は既存Bridgeと同じです。

