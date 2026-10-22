// End-to-end check of the full demo loop in a real browser (installed Edge, throwaway profile).
// Needs Orthanc (seeded), backend :8000 and `npm run dev` :5173 running.
//   node e2e/demo-flow.mjs [screenshotDir]
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright-core';

const OUT = process.argv[2] ?? 'e2e-out';
mkdirSync(OUT, { recursive: true });
// A point inside the liver of PANCREAS_0001 (LPS mm), found via the backend smoke test.
const LIVER = [147.8, -210.5, -63.0];

const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--use-angle=d3d11'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('pageerror', (e) => errors.push(String(e)));

const status = () => page.locator('#status').innerText();
async function waitStatus(re, timeout = 300_000) {
  await page.waitForFunction(
    (src) => new RegExp(src).test(document.querySelector('#status')?.textContent ?? ''),
    re.source,
    { timeout, polling: 500 },
  );
  return status();
}
const shot = (name) => page.screenshot({ path: join(OUT, `${name}.png`) });
const volumeOf = async (name) => {
  const row = page.locator('#segment-list li', { has: page.locator('.name', { hasText: new RegExp(`^${name}`) }) });
  return parseFloat(await row.first().locator('.vol').innerText());
};
const step = (msg) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s] ${msg}`);
const t0 = Date.now();

try {
  await page.goto('http://localhost:5173');
  await waitStatus(/CT series in PACS/, 60_000);
  step(`series list: ${await status()}`);

  const value = await page.locator('#series-picker option').nth(1).getAttribute('value');
  await page.selectOption('#series-picker', value);
  step(await waitStatus(/^Loaded \d+ slices/, 120_000));
  await page.waitForTimeout(1500);
  await shot('1-loaded');

  await page.click('#btn-auto');
  step(await waitStatus(/^TotalSegmentator .* structures/));
  await page.waitForTimeout(1500);
  await shot('2-auto');
  step(`liver ${await volumeOf('liver')} mL, spleen ${await volumeOf('spleen')} mL, pancreas ${await volumeOf('pancreas')} mL`);

  // Click-to-segment at the liver point, through the real mouse path.
  await page.click('[data-mode="prompt"]');
  const pos = await page.evaluate(async (w) => {
    const vp = window.__demo.engine.getViewport('axial');
    const cam = vp.getCamera();
    const n = cam.viewPlaneNormal;
    const d = (w[0] - cam.focalPoint[0]) * n[0] + (w[1] - cam.focalPoint[1]) * n[1] + (w[2] - cam.focalPoint[2]) * n[2];
    vp.setCamera({
      focalPoint: cam.focalPoint.map((f, i) => f + d * n[i]),
      position: cam.position.map((p, i) => p + d * n[i]),
    });
    vp.render();
    await new Promise((r) => setTimeout(r, 300));
    const [x, y] = vp.worldToCanvas(w);
    const r = vp.element.getBoundingClientRect();
    return { x: r.left + x, y: r.top + y };
  }, LIVER);
  await page.mouse.click(pos.x, pos.y);
  step(await waitStatus(/^MedSAM2 \(.*\) .* mL/));
  await page.waitForTimeout(1000);
  await shot('3-prompt');
  const region = await volumeOf('region_1');
  step(`region_1 (click in liver) ${region} mL`);

  // Brush on the prompt segment, then undo.
  await page.locator('#segment-list li', { hasText: 'region_1' }).click();
  await page.click('[data-mode="brush"]');
  await page.mouse.move(pos.x + 60, pos.y + 60);
  await page.mouse.down();
  await page.mouse.move(pos.x + 120, pos.y + 80, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(1500);
  const afterBrush = await volumeOf('region_1');
  await shot('4-brush');
  await page.click('#btn-undo');
  await page.waitForTimeout(1500);
  const afterUndo = await volumeOf('region_1');
  step(`brush: ${region} -> ${afterBrush} mL, undo -> ${afterUndo} mL`);

  await page.click('[data-mode="navigate"]');
  await page.click('#btn-export');
  step(await waitStatus(/^Stored DICOM-SEG|^Error/, 120_000));
  await page.click('#btn-reload-seg');
  step(await waitStatus(/^Reloaded SEG|^Error/, 120_000));
  await page.waitForTimeout(1500);
  await shot('5-reloaded');
} catch (e) {
  step(`FAILED: ${e.message}\nstatus: ${await status().catch(() => '?')}`);
  await shot('error');
  process.exitCode = 1;
} finally {
  console.log(errors.length ? `console errors (${errors.length}):\n  ${errors.slice(0, 15).join('\n  ')}` : 'no console errors');
  await browser.close();
}
