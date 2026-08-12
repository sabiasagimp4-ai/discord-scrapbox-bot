# Scrapbox動画のEagle一括取り込み 設計書

作成日: 2026-08-12

## 目的

Discordから指示すると、Scrapboxプロジェクト内の全ページを走査し、ページ本文に含まれる動画URLをダウンロードして、Eagleの動画ファイルとして登録する。

この機能は、既存のBotがRender上で動作し、EagleがユーザーのWindows PC上で動作する構成を前提とする。そのため、RenderからユーザーPCの`localhost`へ接続することはせず、ユーザーPC上のBridgeがRenderのBotへHTTPSでポーリングする。

## ユーザー向け操作

### `/eagle import-all`

1. BotがScrapboxプロジェクトの全ページを列挙する。
2. 各ページの生の本文を取得し、動画URLを抽出する。
3. URLを正規化して同一URLを重複排除する。
4. 「ページ数、動画URL数、重複排除後の件数、対象外／取得失敗ページ数」をDMでプレビューする。
5. 実際のダウンロードは、明示的な確認を受けるまで開始しない。

### `/eagle import-all confirm:true`

プレビュー結果を確認したユーザーが実行すると、抽出済みURLをジョブ化する。ジョブ作成後、Bridgeが順番に処理し、BotはDMに進捗と完了・失敗件数を通知する。

### `/eagle status`

現在の取り込みジョブの件数、処理中、成功、スキップ、失敗を表示する。失敗理由はURL単位で確認できるようにする。

### `/eagle retry-failed`

直近ジョブの失敗URLだけを再キューする。公開状態の変更や一時的なネットワークエラーへの対処を目的とする。

これらのコマンドは既存のBot所有者チェックを適用し、所有者以外から実行できないようにする。

## システム構成

```text
Discord DM
   │ slash command
   ▼
Render上のBot ── Scrapbox API ── 全ページと本文
   ▲  │
   │  │ HTTPS polling / result callback
   │  ▼
Windows Eagle Bridge ── yt-dlp + ffmpeg ── 動画ファイル
   │
   └──────── localhost:41595 ── Eagle Web API
```

### Render上のBot

- Scrapboxのページ一覧と各ページ本文を取得する。
- 本文中の`http://`／`https://` URLから動画候補を抽出する。
- URL、元ページタイトル、ScrapboxページURL、元の行をジョブメタデータに保存する。
- Bridge向けに、認証済みのジョブ取得・結果報告HTTPエンドポイントを提供する。
- 進捗をDiscord DMへ送る。

ジョブは既存アプリのプロセス内状態として保持する。Renderの再起動で未完了ジョブが失われる場合があるため、その場合は`/eagle import-all`を再実行する。Bridge側の完了マニフェストにより、再実行時に既にEagleへ登録済みのURLはスキップする。

### Windows Eagle Bridge

リポジトリ内に独立した`eagle_bridge.py`を追加する。BridgeはユーザーPCで手動起動する常駐プロセスとし、次の処理を行う。

1. Botのジョブ取得エンドポイントを一定間隔でポーリングする。
2. URLごとにyt-dlpで動画を一時ディレクトリへダウンロードする。
3. Eagleが起動していることを確認する。
4. EagleローカルWeb APIの`/api/item/addFromPath`へ、ダウンロード済みファイルを渡す。
5. タイトル、元URL、Scrapboxページ情報、タグをEagleのメタデータとして登録する。
6. 成功・失敗結果をBotへ返し、ローカルマニフェストへ成功URLとEagle登録情報を保存する。
7. 一時ファイルを削除する。

BridgeはEagleのポートを外部公開しない。Botへの接続はBridgeからの外向きHTTPSだけにする。

## ジョブAPI

### `GET /eagle/jobs`

Bridge認証ヘッダー`X-Eagle-Bridge-Token`を要求する。未完了のジョブを最大件数まで返す。各ジョブには次を含める。

- `job_id`
- `canonical_url`
- `source_url`
- `page_title`
- `page_url`
- `source_line`
- `attempts`

Bridgeが一つであることを前提に、取得時に処理中へ遷移させる。取得後にBridgeが落ちたジョブは、一定時間後に再取得可能にする。

### `POST /eagle/jobs/{job_id}/result`

同じ認証ヘッダーを要求する。Bridgeは次を返す。

- `status`: `succeeded` または `failed`
- `title`
- `file_name`
- `eagle_item_id`（取得できる場合）
- `error`（失敗時）

結果報告は同じジョブについて複数回届いても、完了済みを成功から失敗へ戻さない冪等処理にする。

## URL抽出と重複排除

- Scrapboxの既存の要約用テキスト取得関数はURLを除去するため使わず、URLを保持する生本文取得関数を追加する。
- Markdown、Scrapbox記法、通常のテキストに埋め込まれたURLを対象にする。
- URL末尾の句読点や括弧など、本文の一部ではない文字を除去する。
- `#fragment`は除去する。
- YouTubeの`si`や表示用の既知の追跡パラメータなど、動画識別に不要なパラメータだけを除去する。
- `youtu.be`、YouTubeの`watch`／`shorts`／`live`、その他yt-dlpが扱える公開動画URLを候補とする。
- yt-dlpで実際に動画として取得できないURLは失敗として記録し、全体処理は継続する。
- 同一正規化URLが複数ページに現れる場合は1件だけダウンロードし、全ての出典ページをメタデータに残す。

## Eagle登録メタデータ

- Eagleアイテム名: yt-dlpが取得した動画タイトル。取得できなければページタイトル。
- `website`: 元の動画URL。
- `annotation`: Scrapboxページタイトル、ページURL、元の行、取り込み日時。
- tags: `scrapbox`, `video`, およびプロジェクト名と配信元ドメイン。
- フォルダ: 初期実装ではEagleの既定フォルダへ登録する。固定フォルダ指定は後続機能にする。

## 失敗・安全性・制限

- ダウンロードは公開動画を対象とし、YouTube APIやCookieを使わない。
- 非公開、年齢制限、会員限定、地域制限、ログイン必須の動画は失敗する可能性がある。
- Eagleが起動していない場合はジョブを失敗扱いにせず、Bridgeが一定回数待ってから結果を返す。
- 同時ダウンロード数は初期値1とし、ディスク・帯域・Eagle APIへの負荷を抑える。
- 各ジョブにはタイムアウトと最大リトライ回数を設ける。
- 一時ダウンロード先はBridgeの専用ディレクトリに限定し、処理後に削除する。
- BotとBridge間のトークンは環境変数から読み、ログやDiscordメッセージへ出力しない。
- Bot側のAPIは動画URLやファイルを中継せず、ジョブメタデータだけを返す。

## 追加・変更するファイルの想定

- `eagle_import.py`: URL抽出、正規化、ジョブ状態、取り込み対象の組み立て。
- `scrapbox_search.py`: URLを保持したページ本文取得。
- `bot.py`: `/eagle`コマンド、ジョブAPI、Discord DM進捗通知。
- `eagle_bridge.py`: Windows上のダウンロードとEagle登録。
- `requirements.txt`: Bridge実行に必要な依存関係の整理。
- `README.md`: Eagle、yt-dlp、ffmpeg、環境変数、Bridge起動方法、コマンド説明。
- `tests/`: URL抽出、正規化、ページ走査、認証、ジョブ遷移、再試行、BridgeのEagle API呼び出しのテスト。

## 受け入れ条件

1. 所有者がプレビューを確認するまでダウンロードが始まらない。
2. 全ページの本文から動画URLを抽出し、同一URLを1回だけジョブ化できる。
3. Bridgeが公開動画をダウンロードし、Eagleの動画ファイルとして登録できる。
4. Eagle未起動、動画取得失敗、Eagle API失敗を個別に記録し、他ジョブを継続できる。
5. 再実行時にBridgeの成功マニフェストで既登録URLをスキップできる。
6. BotとBridgeのエンドポイントがトークンなしでは利用できない。
7. 既存テストを壊さず、追加テストと全体テストが通る。
8. READMEだけでWindows側のセットアップと実行が再現できる。

## 今回は実装しないこと

- YouTube Data API、Cookie、ログインセッションの利用。
- Eagleの外部公開やポートフォワーディング。
- Eagleフォルダの自動分類、サムネイル編集、動画変換の詳細設定。
- Render再起動をまたぐクラウド側の永続ジョブキュー。
- ライブ配信の即時検知。
