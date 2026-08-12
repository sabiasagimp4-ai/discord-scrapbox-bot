const { promises: fs, readFileSync, writeFileSync } = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

const TRACKING_KEYS = new Set(['app', 'feature', 'si']);
const DEFAULT_MANIFEST_NAME = 'scrapbox-video-import-manifest.json';

function canonicalizeUrl(value) {
  let url;
  try {
    url = new URL(String(value || '').trim());
  } catch {
    return null;
  }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return null;

  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING_KEYS.has(key.toLowerCase())) url.searchParams.delete(key);
  }
  url.hash = '';
  const host = url.hostname.toLowerCase();
  if (['youtu.be', 'www.youtu.be'].includes(host)) {
    const id = url.pathname.split('/').filter(Boolean)[0];
    return id ? `https://youtu.be/${id}` : null;
  }
  if (['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(host)) {
    if (url.pathname === '/watch') {
      const id = url.searchParams.get('v');
      return id ? `https://youtu.be/${id}` : null;
    }
    const parts = url.pathname.split('/').filter(Boolean);
    if (parts.length === 2 && ['shorts', 'live'].includes(parts[0])) {
      return `https://youtu.be/${parts[1]}`;
    }
  }
  url.protocol = url.protocol.toLowerCase();
  url.hostname = host;
  return url.toString();
}

function buildYtDlpArgs(url, outputDir) {
  const normalized = String(outputDir).replace(/[\\/]$/, '');
  return [
    '--no-playlist',
    '--no-part',
    '--merge-output-format', 'mp4',
    '-f', 'bestvideo*+bestaudio/best',
    '-o', `${normalized}/%(id)s.%(ext)s`,
    url,
  ];
}

function parseManifest(value) {
  try {
    const parsed = JSON.parse(value || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

class ManifestStore {
  constructor({ filePath = '', read = null, write = null } = {}) {
    this.filePath = filePath;
    this.read = read || (() => {
      try { return readFileSync(filePath, 'utf8'); } catch { return '{}'; }
    });
    this.write = write || ((value) => writeFileSync(filePath, value));
    this.entries = parseManifest(this.read());
  }

  has(url) {
    return Boolean(this.entries[canonicalizeUrl(url) || url]);
  }

  get(url) {
    return this.entries[canonicalizeUrl(url) || url] || null;
  }

  record(url, value) {
    const key = canonicalizeUrl(url) || url;
    if (this.entries[key]) return this.entries[key];
    this.entries[key] = { ...value };
    this.write(JSON.stringify(this.entries, null, 2));
    return this.entries[key];
  }
}

class BotClient {
  constructor(botUrl, token, fetchImpl = null) {
    this.botUrl = String(botUrl || '').replace(/\/$/, '');
    this.token = token;
    const request = fetchImpl || globalThis.fetch;
    this.fetchImpl = typeof request === 'function' ? request.bind(globalThis) : request;
    if (!this.botUrl || !this.token || typeof this.fetchImpl !== 'function') {
      throw new Error('Bot URL、トークン、fetchが必要です');
    }
  }

  async request(route, { method = 'GET', body } = {}) {
    const response = await this.fetchImpl(`${this.botUrl}${route}`, {
      method,
      headers: {
        'X-Eagle-Bridge-Token': this.token,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error('Bot APIからJSON形式のレスポンスが返りませんでした。Renderのデプロイ状態を確認してください');
    }
    if (!response.ok) throw new Error(payload.error || `Bot API error: ${response.status}`);
    return payload;
  }

  preview() { return this.request('/eagle/preview', { method: 'POST' }); }
  confirm(previewId) { return this.request('/eagle/confirm', { method: 'POST', body: { preview_id: previewId } }); }
  status() { return this.request('/eagle/status'); }
  jobs(limit = 1) { return this.request(`/eagle/jobs?limit=${encodeURIComponent(limit)}`); }
  result(jobId, payload) {
    return this.request(`/eagle/jobs/${encodeURIComponent(jobId)}/result`, { method: 'POST', body: payload });
  }
}

async function downloadWithYtDlp(url, outputDir) {
  await fs.mkdir(outputDir, { recursive: true });
  const before = new Set(await fs.readdir(outputDir));
  const args = buildYtDlpArgs(url, outputDir);
  await new Promise((resolve, reject) => {
    const child = spawn('yt-dlp', args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error((stderr || `yt-dlp exited with ${code}`).trim().slice(-500)));
    });
  });
  const after = await fs.readdir(outputDir);
  const candidates = after.filter((name) => !before.has(name) && name.toLowerCase().endsWith('.mp4'));
  if (!candidates.length) throw new Error('yt-dlpは完了しましたがMP4ファイルが見つかりません');
  return { path: path.join(outputDir, candidates[candidates.length - 1]) };
}

function annotationFor(job) {
  const sources = Array.isArray(job.sources) && job.sources.length ? job.sources : [job];
  return sources.map((source) => [
    `Scrapbox: ${source.page_title || ''}`,
    source.page_url || '',
    `元の行: ${source.source_line || ''}`,
  ].join('\n')).join('\n\n');
}

async function removeFile(filePath) {
  if (!filePath) return;
  try { await fs.rm(filePath, { force: true }); } catch { /* cleanup is best effort */ }
}

async function processJob(job, { manifest, bot, eagleApi = globalThis.eagle, download = downloadWithYtDlp, tempRoot = os.tmpdir() } = {}) {
  const canonical = canonicalizeUrl(job.canonical_url) || job.canonical_url;
  if (manifest.has(canonical)) {
    return { status: 'succeeded', skipped: true, ...manifest.get(canonical) };
  }
  if (!eagleApi?.item?.addFromPath) throw new Error('Eagle Item APIが利用できません');

  const tempDir = await fs.mkdtemp(path.join(tempRoot, 'eagle-scrapbox-'));
  let downloaded;
  try {
    downloaded = await download(canonical, tempDir);
    const title = downloaded.title || job.page_title || canonical;
    const itemId = await eagleApi.item.addFromPath(downloaded.path, {
      name: title,
      website: job.source_url || canonical,
      tags: ['scrapbox', 'video'],
      folders: [],
      annotation: annotationFor(job),
    });
    const result = { title, file_name: path.basename(downloaded.path), eagle_item_id: itemId || '' };
    manifest.record(canonical, result);
    return { status: 'succeeded', ...result };
  } finally {
    if (downloaded?.path) await removeFile(downloaded.path);
    try { await fs.rm(tempDir, { recursive: true, force: true }); } catch { /* cleanup is best effort */ }
  }
}

async function processPendingJobs({ bot, manifest, eagleApi = globalThis.eagle, log = () => {} } = {}) {
  const { jobs = [] } = await bot.jobs(1);
  const results = [];
  for (const job of jobs) {
    try {
      const result = await processJob({ ...job }, { manifest, bot, eagleApi });
      await bot.result(job.job_id, result);
      results.push(result);
      log(`完了: ${job.page_title || job.canonical_url}`);
    } catch (error) {
      const result = { status: 'failed', error: String(error).slice(0, 500) };
      await bot.result(job.job_id, result);
      results.push(result);
      log(`失敗: ${result.error}`);
    }
  }
  return results;
}

function configStore() {
  const storage = globalThis.localStorage;
  return {
    load() {
      try { return JSON.parse(storage?.getItem('scrapbox-eagle-config') || '{}'); } catch { return {}; }
    },
    save(value) {
      try { storage?.setItem('scrapbox-eagle-config', JSON.stringify(value)); } catch { /* optional */ }
    },
  };
}

function setButtonBusy(button, busy, label) {
  if (!button) return;
  button.disabled = busy;
  if (label) button.textContent = label;
}

async function startUi() {
  if (typeof document === 'undefined') return;
  const $ = (id) => document.getElementById(id);
  const saved = configStore().load();
  $('bot-url').value = saved.botUrl || '';
  $('token').value = saved.token || '';
  const log = (message) => {
    $('log').textContent = `${new Date().toLocaleTimeString()} ${message}\n${$('log').textContent}`.slice(0, 6000);
  };
  const client = () => {
    const config = { botUrl: $('bot-url').value.trim(), token: $('token').value.trim() };
    configStore().save(config);
    return new BotClient(config.botUrl, config.token);
  };
  const manifest = new ManifestStore({ filePath: path.join(os.homedir(), DEFAULT_MANIFEST_NAME) });
  let previewId = null;
  let running = false;
  let scanRunning = false;

  $('scan').onclick = async () => {
    if (scanRunning) return;
    scanRunning = true;
    setButtonBusy($('scan'), true, 'スキャン中…');
    $('confirm').disabled = true;
    $('summary').textContent = '全ページをスキャン中…ページ数によって時間がかかります。';
    log('全ページスキャンを開始しました。完了までこの画面を開いたままにしてください。');
    try {
      const preview = await client().preview();
      previewId = preview.preview_id;
      $('confirm').disabled = false;
      $('summary').textContent = `ページ ${preview.page_count}件 / 動画 ${preview.video_count}件 / 取得失敗 ${preview.failed_page_count}件`;
      $('sources').textContent = preview.sources.map((source) => `${source.canonical_url}\n  ${source.sources.map((item) => item.page_title).join(', ')}`).join('\n');
      log(`プレビュー作成: ${preview.video_count}件`);
    } catch (error) {
      log(`スキャン失敗: ${error}`);
    } finally {
      scanRunning = false;
      setButtonBusy($('scan'), false, '全ページをスキャン');
    }
  };
  $('confirm').onclick = async () => {
    if (!previewId) return;
    setButtonBusy($('confirm'), true, 'キューへ追加中…');
    log('確認済みプレビューを取り込みキューへ追加しています。');
    try {
      const result = await client().confirm(previewId);
      $('confirm').disabled = true;
      log(`取り込みキューへ ${result.jobs_created}件追加`);
      await refresh();
    } catch (error) {
      log(`確認失敗: ${error}`);
      $('confirm').disabled = false;
    }
  };
  const refresh = async () => {
    try {
      const status = await client().status();
      const counts = status.counts;
      $('summary').textContent = `待機 ${counts.pending} / 実行中 ${counts.running} / 成功 ${counts.succeeded} / 失敗 ${counts.failed}`;
    } catch (error) { log(`状態取得失敗: ${error}`); }
  };
  $('refresh').onclick = refresh;
  $('start').onclick = async () => {
    if (running) return;
    running = true;
    setButtonBusy($('start'), true, '取り込み中…');
    log('取り込みを開始しました。待機ジョブを確認しています。');
    try {
      const bot = client();
      while (running) {
        const results = await processPendingJobs({ bot, manifest, eagleApi: globalThis.eagle, log });
        await refresh();
        if (!results.length) {
          log('待機中のジョブがありません。先にスキャン後、プレビューを確認してください。');
          break;
        }
      }
      log('取り込み処理を終了しました');
    } catch (error) { log(`取り込み開始失敗: ${error}`); }
    finally {
      running = false;
      setButtonBusy($('start'), false, '取り込み開始');
    }
  };
  await refresh();
}

if (typeof module !== 'undefined') {
  module.exports = {
    canonicalizeUrl,
    buildYtDlpArgs,
    ManifestStore,
    BotClient,
    processJob,
    processPendingJobs,
    setButtonBusy,
  };
}

if (typeof window !== 'undefined') {
  const boot = () => {
    startUi().catch((error) => {
      console.error(error);
      const log = document.getElementById('log');
      if (log) log.textContent = `初期化失敗: ${error}`;
    });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
}
