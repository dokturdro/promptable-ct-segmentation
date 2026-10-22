import { cache, utilities as csUtils, volumeLoader, type Types } from '@cornerstonejs/core';
import { Enums as ToolEnums, segmentation, type Types as ToolTypes } from '@cornerstonejs/tools';

import { mapBackendToVolume, scatter, gather, type AxisMapping, type BackendGeometry } from './geometry';
import { MPR_VIEWPORT_IDS } from './viewer';

export type SegmentSource = 'auto' | 'prompt' | 'manual';

export interface SegmentState {
  label: number; // = Cornerstone segment index = voxel value
  name: string;
  color: [number, number, number];
  visible: boolean;
  opacity: number; // 0..1
  source: SegmentSource;
  edited: boolean; // touched by brush/eraser/prompt after creation
}

type CsSegment = ToolTypes.Segment;

const { DefaultHistoryMemo } = csUtils.HistoryMemo;
const FIRST_USER_LABEL = 200; // TotalSegmentator uses 1..117

let segmentationId: string | undefined;
let labelmap: Types.IImageVolume | undefined;
let ctVolume: Types.IImageVolume | undefined;
export const segments = new Map<number, SegmentState>();
const listeners = new Set<() => void>();

export const onSegmentsChanged = (fn: () => void) => listeners.add(fn);
export const notify = () => listeners.forEach((fn) => fn());

export function getSegmentationId(): string | undefined {
  return segmentationId;
}

export function getCtVolume(): Types.IImageVolume | undefined {
  return ctVolume;
}

/** Creates an empty labelmap on the CT grid and shows it in the MPR viewports. */
export async function initSegmentation(volume: Types.IImageVolume): Promise<void> {
  if (segmentationId) {
    segmentation.removeAllSegmentationRepresentations();
    segmentation.removeAllSegmentations();
    if (labelmap) cache.removeVolumeLoadObject(labelmap.volumeId);
  }
  segments.clear();
  ctVolume = volume;
  segmentationId = `seg-${volume.volumeId.split(':').pop()}`;
  labelmap = volumeLoader.createAndCacheDerivedLabelmapVolume(volume.volumeId, {
    volumeId: `${segmentationId}-labelmap`,
  });
  segmentation.addSegmentations([
    {
      segmentationId,
      representation: {
        type: ToolEnums.SegmentationRepresentations.Labelmap,
        data: { volumeId: labelmap.volumeId },
      },
      config: { label: 'Demo segmentation' },
    },
  ]);
  const map: Record<string, { segmentationId: string }[]> = {};
  for (const id of MPR_VIEWPORT_IDS) map[id] = [{ segmentationId }];
  segmentation.addLabelmapRepresentationToViewportMap(map);
  notify();
}

function voxels(): Types.IVoxelManager<number> {
  if (!labelmap?.voxelManager) throw new Error('no labelmap');
  return labelmap.voxelManager as Types.IVoxelManager<number>;
}

function mappingFor(geometry: BackendGeometry): AxisMapping {
  if (!labelmap) throw new Error('no labelmap');
  return mapBackendToVolume(geometry, labelmap);
}

function dataChanged(): void {
  if (!segmentationId) return;
  segmentation.triggerSegmentationEvents.triggerSegmentationDataModified(segmentationId);
}

/** Applies a sparse voxel diff and records it in the shared undo history (with brush edits). */
function commitDiff(indices: number[], before: number[], after: number[], what: string): void {
  if (!indices.length) return;
  const idx = Uint32Array.from(indices);
  const old = Uint8Array.from(before);
  const neu = Uint8Array.from(after);
  const apply = (values: Uint8Array) => {
    const vm = voxels();
    const data = vm.getCompleteScalarDataArray!() as Uint8Array;
    for (let n = 0; n < idx.length; n++) data[idx[n]] = values[n];
    vm.setCompleteScalarDataArray!(data);
    dataChanged();
    notify();
  };
  apply(neu);
  DefaultHistoryMemo.push({ id: what, restoreMemo: (undo) => apply(undo ? old : neu) });
}

export function upsertSegment(s: Omit<SegmentState, 'visible' | 'opacity' | 'edited'> & Partial<SegmentState>) {
  const prev = segments.get(s.label);
  segments.set(s.label, {
    visible: true,
    opacity: 0.6,
    edited: false,
    ...prev,
    ...s,
  });
  if (segmentationId) {
    const all: Record<number, CsSegment> = {};
    for (const seg of segments.values()) {
      all[seg.label] = { segmentIndex: seg.label, label: seg.name, locked: false, cachedStats: {}, active: false };
    }
    segmentation.updateSegmentations([{ segmentationId, payload: { segments: all } }]);
  }
  applyStyle(s.label);
}

export function applyStyle(label: number): void {
  const s = segments.get(label);
  if (!s || !segmentationId) return;
  for (const vp of MPR_VIEWPORT_IDS) {
    segmentation.config.color.setSegmentIndexColor(vp, segmentationId, label, [
      ...s.color,
      Math.round(s.opacity * 255),
    ] as Types.Color);
    segmentation.config.visibility.setSegmentIndexVisibility(
      vp,
      { segmentationId, type: ToolEnums.SegmentationRepresentations.Labelmap },
      label,
      s.visible,
    );
  }
}

/** Replaces the whole labelmap with an automatic result (backend (k, j, i) order). */
export function applyAutoResult(
  geometry: BackendGeometry,
  labels: Uint8Array,
  segs: { label: number; name: string; color: [number, number, number] }[],
): void {
  const m = mappingFor(geometry);
  const data = voxels().getCompleteScalarDataArray!() as Uint8Array;
  const idx: number[] = [];
  const before: number[] = [];
  const after: number[] = [];
  scatter(m, labels, (dst, v) => {
    if (data[dst] !== v) {
      idx.push(dst);
      before.push(data[dst]);
      after.push(v);
    }
  });
  for (const s of segs) upsertSegment({ ...s, source: 'auto' });
  commitDiff(idx, before, after, 'auto-segmentation');
}

/**
 * Writes a prompt result (cropped binary mask) as segment `label`: voxels inside the mask
 * become `label`; voxels of `label` outside the mask are cleared (the prompt replaces it).
 */
export function applyPromptResult(
  geometry: BackendGeometry,
  crop: { start: [number, number, number]; shape: [number, number, number] },
  mask: Uint8Array,
  label: number,
): void {
  const m = mappingFor(geometry);
  const data = voxels().getCompleteScalarDataArray!() as Uint8Array;
  // Build the desired state in a copy (typed-array passes are fast), then diff once.
  const next = Uint8Array.from(data);
  for (let p = 0; p < next.length; p++) if (next[p] === label) next[p] = 0;
  scatter(m, mask, (dst, v) => {
    if (v) next[dst] = label;
  }, crop.start, crop.shape);
  const idx: number[] = [];
  const before: number[] = [];
  const after: number[] = [];
  for (let p = 0; p < data.length; p++) {
    if (next[p] !== data[p]) {
      idx.push(p);
      before.push(data[p]);
      after.push(next[p]);
    }
  }
  commitDiff(idx, before, after, `prompt-${label}`);
}

export function nextUserLabel(): number {
  let l = FIRST_USER_LABEL;
  while (segments.has(l)) l++;
  if (l > 255) throw new Error('too many segments');
  return l;
}

/** Labelmap in backend (k, j, i) order for export. */
export function exportLabels(geometry: BackendGeometry): Uint8Array {
  return gather(mappingFor(geometry), voxels().getCompleteScalarDataArray!());
}

/** Per-label volume in mL from the current labelmap voxels and spacing. */
export function computeVolumesMl(): Map<number, number> {
  if (!labelmap) return new Map();
  const data = voxels().getCompleteScalarDataArray!() as Uint8Array;
  const counts = new Uint32Array(256);
  for (let p = 0; p < data.length; p++) counts[data[p]]++;
  const voxelMl = (labelmap.spacing[0] * labelmap.spacing[1] * labelmap.spacing[2]) / 1000;
  const out = new Map<number, number>();
  for (let v = 1; v < 256; v++) if (counts[v]) out.set(v, counts[v] * voxelMl);
  return out;
}

export function undo(): void {
  DefaultHistoryMemo.undo();
}

export function redo(): void {
  DefaultHistoryMemo.redo();
}
