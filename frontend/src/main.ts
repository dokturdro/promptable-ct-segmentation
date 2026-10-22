import { Enums as ToolEnums } from '@cornerstonejs/tools';
import { eventTarget } from '@cornerstonejs/core';

import * as api from './api';
import { createImageIds, listSeries, type SeriesInfo } from './dicomweb';
import type { BackendGeometry } from './geometry';
import { clearPrompts, getPromptState, initPromptInteraction } from './prompt';
import * as seg from './segmentation';
import { initEditTools, setActiveSegment, setBrushSize, setMode, type Mode } from './tools';
import { createViewer, initCornerstone, loadVolume } from './viewer';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const statusEl = $('status');
const picker = $<HTMLSelectElement>('series-picker');

let current: SeriesInfo | undefined;
let geometry: BackendGeometry | undefined; // backend grid of the current series
let promptTarget: number | null = null; // label being refined by click-to-segment
let promptBusy = false;
let promptQueued = false;
let targetLabel: number | null = null; // segment edited by brush/eraser
let volumes = new Map<number, number>();

function status(msg: string): void {
  statusEl.textContent = msg;
}

function fail(e: unknown): void {
  console.error(e);
  status(`Error: ${e instanceof Error ? e.message : String(e)}`);
}

// --- segment list --------------------------------------------------------------------------

function renderSegments(): void {
  const list = $('segment-list');
  const filter = $<HTMLInputElement>('segment-filter').value.toLowerCase();
  const items = [...seg.segments.values()]
    .filter((s) => (volumes.get(s.label) ?? 0) > 0 || s.source !== 'auto')
    .sort((a, b) => a.name.localeCompare(b.name));
  $('segment-count').textContent = items.length ? `(${items.length})` : '';
  list.replaceChildren(
    ...items
      .filter((s) => s.name.toLowerCase().includes(filter))
      .map((s) => {
        const li = document.createElement('li');
        li.classList.toggle('target', s.label === targetLabel);
        const vis = Object.assign(document.createElement('input'), { type: 'checkbox', checked: s.visible });
        vis.title = 'Show/hide';
        vis.addEventListener('click', (e) => e.stopPropagation());
        vis.addEventListener('change', () => {
          s.visible = vis.checked;
          seg.applyStyle(s.label);
        });
        const sw = document.createElement('span');
        sw.className = 'swatch';
        sw.style.background = `rgb(${s.color.join(',')})`;
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = s.name;
        if (s.source !== 'auto' || s.edited) {
          const b = document.createElement('span');
          b.className = 'badge';
          b.textContent = s.source === 'auto' ? 'edited' : s.source;
          name.append(b);
        }
        const vol = document.createElement('span');
        vol.className = 'vol';
        vol.textContent = `${(volumes.get(s.label) ?? 0).toFixed(1)} mL`;
        const op = Object.assign(document.createElement('input'), {
          type: 'range', min: '0', max: '100', value: String(Math.round(s.opacity * 100)),
        });
        op.title = 'Opacity';
        op.addEventListener('click', (e) => e.stopPropagation());
        op.addEventListener('input', () => {
          s.opacity = Number(op.value) / 100;
          seg.applyStyle(s.label);
        });
        li.append(vis, sw, name, vol, op);
        li.addEventListener('click', () => selectTarget(s.label));
        return li;
      }),
  );
}

let volumeTimer: number | undefined;
function refreshVolumes(): void {
  window.clearTimeout(volumeTimer);
  volumeTimer = window.setTimeout(() => {
    volumes = seg.computeVolumesMl();
    renderSegments();
  }, 250);
}

function selectTarget(label: number): void {
  targetLabel = label;
  promptTarget = label;
  const id = seg.getSegmentationId();
  if (id) setActiveSegment(id, label);
  $('prompt-target').textContent = seg.segments.get(label)?.name ?? 'new segment';
  renderSegments();
}

// --- series loading ------------------------------------------------------------------------

async function loadSeries(s: SeriesInfo): Promise<void> {
  current = s;
  geometry = undefined;
  clearPrompts();
  promptTarget = targetLabel = null;
  $('prompt-target').textContent = 'new segment';
  status('Fetching metadata…');
  const imageIds = await createImageIds(s.studyUID, s.seriesUID);
  const t0 = performance.now();
  const volume = await loadVolume(s.seriesUID, imageIds, (n, total) => status(`Loading slices ${n}/${total}`));
  await seg.initSegmentation(volume);
  volumes = new Map();
  renderSegments();
  ($('btn-auto') as HTMLButtonElement).disabled = false;
  ($('btn-export') as HTMLButtonElement).disabled = false;
  ($('btn-reload-seg') as HTMLButtonElement).disabled = false;
  status(`Loaded ${imageIds.length} slices in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
}

// --- AI actions ----------------------------------------------------------------------------

async function runAuto(): Promise<void> {
  if (!current) return;
  const btn = $<HTMLButtonElement>('btn-auto');
  btn.disabled = true;
  try {
    const { jobId } = await api.submitAuto(current.studyUID, current.seriesUID);
    const job = await api.waitForJob<api.AutoResult>(jobId, (j) =>
      status(`Auto-segmentation: ${j.message} (${j.elapsed}s)`),
    );
    const r = job.result!;
    status('Downloading label map…');
    const labels = await api.fetchJobLabelmap(jobId);
    geometry = r.geometry;
    seg.applyAutoResult(r.geometry, labels, r.segments);
    volumes = seg.computeVolumesMl();
    renderSegments();
    const liver = r.segments.find((x) => x.name === 'liver');
    status(
      `TotalSegmentator ${r.meta.mode} on ${r.meta.device}: ${r.meta.seconds.toFixed(0)} s, ` +
        `${r.segments.length} structures` + (liver ? `, liver ${liver.volumeMl.toFixed(0)} mL` : ''),
    );
  } catch (e) {
    fail(e);
  } finally {
    btn.disabled = false;
  }
}

async function runPrompt(): Promise<void> {
  if (!current) return;
  if (promptBusy) {
    promptQueued = true; // re-run with the latest points when the current job finishes
    return;
  }
  const { points, box } = getPromptState();
  if (!box && !points.some((p) => p.label === 1)) return;
  promptBusy = true;
  try {
    const { jobId } = await api.submitPrompt(current.studyUID, current.seriesUID, points, box);
    const job = await api.waitForJob<api.PromptResult>(jobId, (j) => status(`MedSAM2: ${j.message}`));
    const r = job.result!;
    geometry = r.geometry;
    if (r.empty) {
      status('MedSAM2 returned an empty mask; try another click or a box.');
      return;
    }
    if (promptTarget === null) {
      promptTarget = seg.nextUserLabel();
      seg.upsertSegment({
        label: promptTarget,
        name: `region_${promptTarget - 199}`,
        color: [255, 215, 0],
        source: 'prompt',
      });
      selectTarget(promptTarget);
    } else {
      const s = seg.segments.get(promptTarget)!;
      s.edited = true;
      if (s.source === 'manual') s.source = 'prompt';
    }
    const mask = await api.fetchJobLabelmap(jobId);
    seg.applyPromptResult(r.geometry, r.crop!, mask, promptTarget);
    status(`MedSAM2 (${r.meta.device}) ${r.meta.seconds.toFixed(1)} s → ${r.volumeMl} mL`);
  } catch (e) {
    fail(e);
  } finally {
    promptBusy = false;
    if (promptQueued) {
      promptQueued = false;
      void runPrompt();
    }
  }
}

// --- export --------------------------------------------------------------------------------

async function runExport(): Promise<void> {
  if (!current) return;
  geometry ??= await api.seriesGeometry(current.studyUID, current.seriesUID);
  volumes = seg.computeVolumesMl();
  const present = [...seg.segments.values()].filter((s) => (volumes.get(s.label) ?? 0) > 0);
  if (!present.length) return fail(new Error('Nothing to export.'));
  const segments: api.ExportSegment[] = present.map((s) => ({
    label: s.label,
    name: s.name,
    color: s.color,
    ...(s.source === 'manual'
      ? { algorithm: 'MANUAL' as const }
      : s.source === 'prompt'
        ? { algorithm: 'SEMIAUTOMATIC' as const, algorithmName: 'MedSAM2', algorithmVersion: 'MedSAM2_latest' }
        : {
            algorithm: s.edited ? ('SEMIAUTOMATIC' as const) : ('AUTOMATIC' as const),
            algorithmName: 'TotalSegmentator',
            algorithmVersion: '2.18.0',
          }),
  }));
  status('Exporting DICOM-SEG…');
  try {
    const labels = seg.exportLabels(geometry);
    const res = await api.exportDicomSeg(current.studyUID, current.seriesUID, segments, labels);
    status(`Stored DICOM-SEG ${res.sopInstanceUID}\n${res.segments.length} segments, ${res.frames} frames`);
  } catch (e) {
    fail(e);
  }
}

/**
 * Round-trip check: loads the newest DICOM-SEG for this study back from the PACS, replacing
 * the current labelmap, and reports per-segment volume differences against what was shown.
 */
async function reloadSeg(): Promise<void> {
  if (!current) return;
  try {
    const segsInPacs = await api.listSegs(current.studyUID);
    if (!segsInPacs.length) return fail(new Error('No DICOM-SEG in the PACS for this study.'));
    const before = seg.computeVolumesMl();
    const names = new Map([...seg.segments.values()].map((s) => [s.label, s.name]));
    const { jobId } = await api.submitSegLoad(current.studyUID, current.seriesUID, segsInPacs[0]);
    const job = await api.waitForJob<api.SegLoadResult>(jobId, (j) => status(`Load SEG: ${j.message}`));
    const r = job.result!;
    const labels = await api.fetchJobLabelmap(jobId);
    geometry = r.geometry;
    seg.applyAutoResult(r.geometry, labels, r.segments);
    for (const s of r.segments) {
      const st = seg.segments.get(s.label)!;
      st.source = s.algorithm === 'MANUAL' ? 'manual' : s.algorithm === 'SEMIAUTOMATIC' ? 'prompt' : 'auto';
    }
    volumes = seg.computeVolumesMl();
    renderSegments();
    const diffs = r.segments
      .map((s) => {
        const b = before.get(s.label) ?? 0;
        return { name: names.get(s.label) ?? s.name, d: Math.abs((volumes.get(s.label) ?? 0) - b) };
      })
      .sort((a, b) => b.d - a.d);
    const worst = diffs[0];
    status(
      `Reloaded SEG ${r.sopInstanceUID.slice(-12)} (${r.segments.length} segments).\n` +
        (before.size
          ? `Max volume change vs. before export: ${worst.d.toFixed(2)} mL (${worst.name})`
          : 'No previous labelmap to compare.'),
    );
  } catch (e) {
    fail(e);
  }
}

// --- wiring --------------------------------------------------------------------------------

function wireUi(): void {
  $('btn-auto').addEventListener('click', () => void runAuto());
  $('btn-export').addEventListener('click', () => void runExport());
  $('btn-reload-seg').addEventListener('click', () => void reloadSeg());
  for (const b of document.querySelectorAll<HTMLButtonElement>('#mode-buttons button')) {
    b.addEventListener('click', () => {
      const m = b.dataset.mode as Mode;
      if ((m === 'brush' || m === 'eraser') && targetLabel === null) {
        status('Select a segment in the list (or "+ Manual segment") before painting.');
        return;
      }
      setMode(m);
      document.querySelectorAll('#mode-buttons button').forEach((x) => x.classList.toggle('active', x === b));
      $('prompt-panel').classList.toggle('hidden', m !== 'prompt');
    });
  }
  const size = $<HTMLInputElement>('brush-size');
  size.addEventListener('input', () => {
    setBrushSize(Number(size.value));
    $('brush-size-label').textContent = `${size.value} mm`;
  });
  setBrushSize(Number(size.value));
  $('btn-undo').addEventListener('click', seg.undo);
  $('btn-redo').addEventListener('click', seg.redo);
  window.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || (e.target as HTMLElement).tagName === 'INPUT') return;
    if (e.key === 'z' || e.key === 'y') {
      e.preventDefault();
      if (e.key === 'z') seg.undo();
      else seg.redo();
    }
  });
  $('btn-prompt-clear').addEventListener('click', clearPrompts);
  $('btn-prompt-new').addEventListener('click', () => {
    clearPrompts();
    promptTarget = null;
    $('prompt-target').textContent = 'new segment';
  });
  $('segment-filter').addEventListener('input', renderSegments);
  const setAllVisible = (v: boolean) => {
    for (const s of seg.segments.values()) {
      s.visible = v;
      seg.applyStyle(s.label);
    }
    renderSegments();
  };
  $('btn-show-all').addEventListener('click', () => setAllVisible(true));
  $('btn-hide-all').addEventListener('click', () => setAllVisible(false));
  $('btn-new-segment').addEventListener('click', () => {
    if (!seg.getSegmentationId()) return;
    const label = seg.nextUserLabel();
    const name = prompt('Segment name', `manual_${label - 199}`)?.trim();
    if (!name) return;
    seg.upsertSegment({ label, name, color: [0, 200, 255], source: 'manual' });
    selectTarget(label);
  });

  // Brush/eraser strokes: mark the active segment as edited and refresh volumes.
  eventTarget.addEventListener(ToolEnums.Events.SEGMENTATION_DATA_MODIFIED, () => {
    if (targetLabel !== null) {
      const s = seg.segments.get(targetLabel);
      if (s && s.source === 'auto') s.edited = true;
    }
    refreshVolumes();
  });
  seg.onSegmentsChanged(refreshVolumes);
}

async function main(): Promise<void> {
  await initCornerstone();
  const engine = createViewer();
  // Dev-only hook for the e2e script (world <-> canvas for clicks on known anatomy).
  if (import.meta.env.DEV) Object.assign(window, { __demo: { engine } });
  initEditTools();
  initPromptInteraction(() => void runPrompt());
  wireUi();

  api
    .health()
    .then((h) => ($('device-info').textContent = `Backend device: ${h.device}`))
    .catch(() => ($('device-info').textContent = 'Backend not reachable (start it on :8000)'));

  const series = (await listSeries()).filter((s) => s.modality === 'CT');
  picker.innerHTML = '<option value="">Select a CT series…</option>';
  for (const s of series) {
    const opt = document.createElement('option');
    opt.value = s.seriesUID;
    opt.textContent = `${s.patientId} · ${s.description || 'CT'} (${s.numInstances} img)`;
    picker.append(opt);
  }
  status(series.length ? `${series.length} CT series in PACS` : 'No CT series found. Run seed_data.');
  picker.addEventListener('change', () => {
    const s = series.find((x) => x.seriesUID === picker.value);
    if (s) loadSeries(s).catch(fail);
  });
}

main().catch(fail);
