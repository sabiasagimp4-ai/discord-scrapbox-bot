const test = require('node:test');
const assert = require('node:assert/strict');
const { mkdtemp, readFile, rm, writeFile } = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const {
  canonicalizeUrl,
  buildYtDlpArgs,
  ManifestStore,
  BotClient,
  processJob,
} = require('../plugin.js');

test('plugin can load in Eagle classic-script runtime', () => {
  assert.equal(typeof canonicalizeUrl, 'function');
  assert.equal(typeof processJob, 'function');
});

test('plugin source is valid as a classic script', async () => {
  const source = await readFile(path.join(__dirname, '..', 'plugin.js'), 'utf8');
  assert.doesNotThrow(() => new vm.Script(source));
});

test('canonicalizeUrl removes tracking query parameters', () => {
  assert.equal(
    canonicalizeUrl('https://www.youtube.com/watch?v=abc12345678&si=tracking#fragment'),
    'https://youtu.be/abc12345678',
  );
});

test('buildYtDlpArgs requests a single merged mp4 file', () => {
  const args = buildYtDlpArgs('https://youtu.be/abc12345678', 'C:/Temp/eagle');

  assert.deepEqual(args, [
    '--no-playlist',
    '--no-part',
    '--merge-output-format', 'mp4',
    '-f', 'bestvideo*+bestaudio/best',
    '-o', 'C:/Temp/eagle/%(id)s.%(ext)s',
    'https://youtu.be/abc12345678',
  ]);
});

test('ManifestStore deduplicates canonical URLs and persists item metadata', () => {
  const writes = [];
  const store = new ManifestStore({
    read: () => JSON.stringify({}),
    write: (value) => writes.push(value),
  });

  assert.equal(store.has('https://youtu.be/x'), false);
  store.record('https://youtu.be/x', { itemId: 'e1', title: 'Video' });
  store.record('https://youtu.be/x', { itemId: 'e2', title: 'Duplicate' });

  assert.equal(store.has('https://youtu.be/x'), true);
  assert.deepEqual(store.get('https://youtu.be/x'), { itemId: 'e1', title: 'Video' });
  assert.equal(writes.length, 1);
});

test('BotClient propagates API error responses', async () => {
  const client = new BotClient('https://bot.example', 'secret', async () => ({
    ok: false,
    status: 401,
    async json() { return { error: 'invalid' }; },
  }));

  await assert.rejects(
    client.status(),
    /invalid/,
  );
});

test('BotClient reports a non-JSON deployment response clearly', async () => {
  const client = new BotClient('https://bot.example', 'secret', async () => ({
    ok: true,
    status: 200,
    async json() { throw new SyntaxError('Unexpected token o'); },
  }));

  await assert.rejects(
    client.status(),
    /JSON形式のレスポンス/,
  );
});

test('processJob registers metadata, reports all Scrapbox sources, and cleans up the file', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'eagle-plugin-test-'));
  const manifestWrites = [];
  const manifest = new ManifestStore({
    read: () => JSON.stringify({}),
    write: (value) => manifestWrites.push(value),
  });
  const added = [];
  try {
    const result = await processJob({
      job_id: 'j1',
      canonical_url: 'https://youtu.be/x',
      source_url: 'https://youtu.be/x',
      page_title: '代表ページ',
      page_url: 'https://scrapbox.io/proj/代表ページ',
      source_line: 'https://youtu.be/x',
      sources: [
        { page_title: '代表ページ', page_url: 'https://scrapbox.io/proj/代表ページ', source_line: 'https://youtu.be/x' },
        { page_title: '別ページ', page_url: 'https://scrapbox.io/proj/別ページ', source_line: 'https://youtu.be/x' },
      ],
    }, {
      manifest,
      tempRoot: root,
      download: async (_url, directory) => {
        const filePath = path.join(directory, 'video.mp4');
        await writeFile(filePath, 'video');
        return { path: filePath, title: 'Video title' };
      },
      eagleApi: {
        item: {
          async addFromPath(filePath, options) {
            added.push({ filePath, options });
            return 'e1';
          },
        },
      },
    });

    assert.equal(result.status, 'succeeded');
    assert.equal(result.eagle_item_id, 'e1');
    assert.match(added[0].options.annotation, /別ページ/);
    assert.equal(manifestWrites.length, 1);
    await assert.rejects(readFile(added[0].filePath));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
