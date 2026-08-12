# Eagle Plugin版 Scrapbox動画取り込み Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eagle内からScrapbox全ページの動画を確認・ダウンロード・Eagle登録できるプラグインを追加する。

**Architecture:** Discord BotはScrapbox走査とジョブ管理を担当し、認証済みHTTP APIを提供する。EagleプラグインはUIとローカルジョブワーカーを担当し、yt-dlp/ffmpegで取得した一時ファイルをEagleの公式Item APIで登録する。既存のPython Bridgeは互換用に残す。

**Tech Stack:** Python 3標準ライブラリ、既存のDiscord Bot/HTTP server、Eagle Plugin API、Eagle内Node.js、ブラウザ標準JavaScript、yt-dlp、ffmpeg。

## Global Constraints

- YouTube API、Cookie、ログイン状態は使用しない。
- Bot APIには既存の `EAGLE_BRIDGE_TOKEN` を必須とする。
- 公開動画のダウンロード可否はyt-dlp依存であり、実動画E2Eは未検証とする。
- 作業ツリーの既存変更を破壊しない。
- Python変更はTDDでテストを先に追加する。

---

### Task 1: Bot APIのプラグイン操作エンドポイント

**Files:**
- Modify: `bot.py`
- Modify: `tests/test_bot.py`

**Interfaces:**
- Consumes: `create_eagle_preview()`, `_eagle_store`, `_eagle_token_valid()`
- Produces: `handle_eagle_preview_request(token)`, `handle_eagle_confirm_request(token, preview_id)`, `handle_eagle_status_request(token)`, `HealthHandler` routes

- [ ] **Step 1: Write the failing tests** for missing token, preview response, confirm response, and status response.
- [ ] **Step 2: Run the focused tests and verify they fail** because the handlers/routes do not exist.
- [ ] **Step 3: Implement the three handlers and authenticated GET/POST routes.** Preview creation may run synchronously in the existing health server thread, and returns the same preview/job fields used by Discord.
- [ ] **Step 4: Run focused Bot tests and verify they pass.**
- [ ] **Step 5: Commit** with `feat: expose Eagle plugin import API`.

### Task 2: Eagle Plugin pure helpers and API client

**Files:**
- Create: `eagle-plugin/manifest.json`
- Create: `eagle-plugin/index.html`
- Create: `eagle-plugin/plugin.js`
- Create: `eagle-plugin/test/plugin.test.js`

**Interfaces:**
- Produces: `canonicalizeUrl(url)`, `buildYtDlpArgs(url, outputDir)`, `ManifestStore`, `BotClient`

- [ ] **Step 1: Write JavaScript tests** for YouTube URL canonicalization, yt-dlp argument generation, manifest deduplication, and API error propagation.
- [ ] **Step 2: Run `node --test eagle-plugin/test/plugin.test.js` and verify the tests fail** because the plugin module does not exist.
- [ ] **Step 3: Implement Node-compatible pure helpers and export them for tests while keeping Eagle runtime globals optional.**
- [ ] **Step 4: Run JavaScript tests and verify they pass.**
- [ ] **Step 5: Commit** with `feat: scaffold Eagle plugin client`.

### Task 3: Eagle plugin UI and local worker

**Files:**
- Modify: `eagle-plugin/index.html`
- Modify: `eagle-plugin/plugin.js`
- Create: `eagle-plugin/README.md`

**Interfaces:**
- Consumes: `BotClient`, `ManifestStore`, `canonicalizeUrl()`, `buildYtDlpArgs()`
- Produces: preview/confirm/status buttons, serial job worker, `eagle.item.addFromPath()` metadata registration

- [ ] **Step 1: Add UI controls** for Bot URL, token, scan, confirm, start/stop, refresh, and status/error log.
- [ ] **Step 2: Implement preview and confirm calls** with explicit user confirmation before jobs are claimed.
- [ ] **Step 3: Implement serial worker** using `spawn('yt-dlp', args)`, temporary output, Eagle `addFromPath`, and result reporting in `finally`.
- [ ] **Step 4: Add local manifest persistence** and skip already-successful canonical URLs.
- [ ] **Step 5: Document Eagle installation and local dependencies** in the plugin README.
- [ ] **Step 6: Run JavaScript tests and syntax checks.**
- [ ] **Step 7: Commit** with `feat: add Eagle Scrapbox video importer plugin`.

### Task 4: Repository documentation and compatibility

**Files:**
- Modify: `README.md`
- Modify: `eagle_bridge.py` only if required for shared behavior or deprecation notes

- [ ] **Step 1: Add plugin setup and operation instructions** and identify the Python Bridge as a fallback.
- [ ] **Step 2: Document required Eagle version, yt-dlp, ffmpeg, token, and known limitations.**
- [ ] **Step 3: Run the complete Python and JavaScript test suites.**
- [ ] **Step 4: Run `py_compile` and `git diff --check`.**
- [ ] **Step 5: Re-index Codebase Memory and verify new plugin/API symbols.**
- [ ] **Step 6: Commit** with `docs: document Eagle plugin setup`.

