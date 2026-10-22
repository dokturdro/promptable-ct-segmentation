// Independent check: open the study in Orthanc's bundled OHIF and load our DICOM-SEG there.
//   node e2e/ohif-check.mjs <studyUID> [screenshotDir]
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright-core';

const [study, OUT = 'e2e-out'] = process.argv.slice(2);
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--use-angle=d3d11'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(`http://127.0.0.1:8042/ohif/viewer?StudyInstanceUIDs=${study}`);
await page.waitForTimeout(12000);
// Dismiss OHIF's onboarding tour and investigational-use banner if present.
for (const name of [/Skip all/i, /Confirm and hide/i]) {
  const b = page.getByText(name);
  if (await b.count()) await b.first().click();
}
await page.screenshot({ path: join(OUT, 'ohif-1-study.png') });

// Our SEG is listed in the study browser by its series description.
const seg = page.getByText(/AI segmentation \(demo/i);
console.log('SEG entries matching:', await seg.count());
if (await seg.count()) {
  await seg.first().dblclick();
  // OHIF previews the SEG, then asks whether to load (hydrate) it for editing.
  const yes = page.getByRole('button', { name: 'Yes', exact: true });
  await yes.waitFor({ timeout: 30000 }).then(() => yes.click(), () => console.log('no hydrate prompt'));
  await page.waitForTimeout(10000);
}
await page.screenshot({ path: join(OUT, 'ohif-2-seg.png') });
const text = await page.locator('body').innerText();
const m = text.match(/(\d+)\s+segments?/i);
console.log('segments text:', m?.[0] ?? '(not found)', '| liver mentioned:', /liver/i.test(text));
console.log(errors.length ? `page errors: ${errors.slice(0, 5).join(' | ')}` : 'no page errors');
await browser.close();
