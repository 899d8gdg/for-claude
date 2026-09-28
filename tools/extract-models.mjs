#!/usr/bin/env node
// Captures the 3D display models (and their textures) a web page loads.
//
// Opens the page in headless Chromium, scrolls it so lazy-loaded viewers
// start, and saves every response that looks like a 3D asset. It also scans
// the HTML/JS for model URLs the viewer did not request yet (other colours,
// trims) and downloads those directly.
//
// Usage: node tools/extract-models.mjs <url> [outDir] [--images] [--wait=ms]

import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const args = process.argv.slice(2);
const flags = new Set(args.filter((a) => a.startsWith('--')));
const [pageUrl, outDir = 'models'] = args.filter((a) => !a.startsWith('--'));
const saveImages = flags.has('--images');
const extraWait = Number([...flags].find((f) => f.startsWith('--wait='))?.split('=')[1] ?? 15000);

if (!pageUrl) {
  console.error('Usage: node tools/extract-models.mjs <url> [outDir] [--images] [--wait=ms]');
  process.exit(1);
}

const MODEL_EXT = /\.(glb|gltf|bin|obj|mtl|fbx|usdz|usd|dae|3ds|stl|ply|drc|ktx2|basis|hdr|exr)$/i;
const IMAGE_EXT = /\.(jpe?g|png|webp|avif)$/i;
const MODEL_TYPES = /^(model\/|application\/(octet-stream|gltf|vnd\.usdz))/i;
const URL_IN_TEXT = /["'`(]((?:https?:)?\/\/[^"'`()\s]+?|\/?[\w\-./%]+?)\.(glb|gltf|usdz|fbx|obj|drc|ktx2|hdr)(\?[^"'`()\s]*)?["'`)]/gi;

const saved = new Map(); // url -> manifest entry
const textBodies = [];

function localPath(url) {
  const u = new URL(url);
  let p = decodeURIComponent(u.pathname);
  if (p.endsWith('/')) p += 'index';
  const safe = p.split('/').filter(Boolean).map((s) => s.replace(/[^\w.\-]/g, '_'));
  return path.join(outDir, u.hostname, ...safe);
}

function classify(url, contentType) {
  const { pathname } = new URL(url);
  if (MODEL_EXT.test(pathname)) return 'model';
  if (IMAGE_EXT.test(pathname) || contentType.startsWith('image/')) return 'image';
  if (MODEL_TYPES.test(contentType) && !/\.(js|css|woff2?|ttf|json|map)$/i.test(pathname)) return 'model';
  return null;
}

async function save(url, body, kind, contentType) {
  if (saved.has(url) || !body?.length) return;
  const file = localPath(url);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, body);
  saved.set(url, { url, file, kind, contentType, bytes: body.length });
  console.log(`[${kind}] ${(body.length / 1024).toFixed(0).padStart(7)} KB  ${url}`);
}

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 2, // ask responsive viewers for their high-res assets
  userAgent:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
});

// context-level listener so models loaded inside viewer iframes are caught too
const pending = [];
context.on('response', (res) => {
  pending.push(
    (async () => {
      const url = res.url();
      if (!url.startsWith('http') || res.status() >= 400) return;
      const contentType = (res.headers()['content-type'] || '').toLowerCase();
      const kind = classify(url, contentType);
      if (/javascript|json|html|text\//.test(contentType)) {
        const body = await res.text().catch(() => null);
        if (body) textBodies.push({ base: url, body });
        // gltf manifests are JSON but are models in their own right
        if (kind === 'model' && body) await save(url, Buffer.from(body), kind, contentType);
        return;
      }
      if (kind === 'model' || (kind === 'image' && saveImages)) {
        const body = await res.body().catch(() => null);
        await save(url, body, kind, contentType);
      }
    })().catch((e) => console.warn(`! ${res.url()}: ${e.message}`)),
  );
});

const page = await context.newPage();
console.log(`Opening ${pageUrl}`);
await page.goto(pageUrl, { waitUntil: 'networkidle', timeout: 90000 }).catch((e) => console.warn(`! goto: ${e.message}`));

// scroll through the page so lazy viewers and galleries initialise
await page.evaluate(async () => {
  for (let y = 0; y < document.body.scrollHeight; y += 600) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 400));
  }
  window.scrollTo(0, 0);
});
await page.waitForTimeout(extraWait);
await Promise.all(pending);

// fetch model URLs referenced in page source/scripts but never requested
const referenced = new Set();
textBodies.push({ base: page.url(), body: await page.content() });
for (const { base, body } of textBodies) {
  for (const m of body.matchAll(URL_IN_TEXT)) {
    try {
      referenced.add(new URL(m[1] + '.' + m[2] + (m[3] ?? ''), base).href);
    } catch {}
  }
}
for (const url of referenced) {
  if (saved.has(url)) continue;
  const res = await context.request.get(url, { headers: { referer: page.url() } }).catch(() => null);
  if (!res?.ok()) {
    console.warn(`! referenced but not downloadable: ${url}`);
    continue;
  }
  await save(url, await res.body(), 'model', res.headers()['content-type'] || '');
}

await browser.close();

const entries = [...saved.values()];
await mkdir(outDir, { recursive: true });
await writeFile(path.join(outDir, 'manifest.json'), JSON.stringify({ page: pageUrl, capturedAt: new Date().toISOString(), files: entries }, null, 2));
const models = entries.filter((e) => e.kind === 'model');
console.log(`\nSaved ${models.length} model file(s)${saveImages ? ` and ${entries.length - models.length} image(s)` : ''} to ${outDir}/`);
if (!models.length) console.log('No 3D assets seen. The viewer may need a click to start; try a longer --wait or open the configurator page directly.');
