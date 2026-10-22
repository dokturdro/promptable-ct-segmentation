import type { BackendGeometry } from './geometry';

export interface Code {
  scheme: string;
  value: string;
  meaning: string;
}

export interface SegmentDto {
  label: number;
  name: string;
  color: [number, number, number];
  category: Code;
  type: Code;
  typeModifier: Code | null;
  volumeMl: number;
}

export interface Job<R> {
  id: string;
  kind: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  message: string;
  elapsed: number;
  result: R | null;
  error: string | null;
}

export interface AutoResult {
  seriesUID: string;
  geometry: BackendGeometry;
  segments: SegmentDto[];
  meta: { engine: string; mode: string; device: string; seconds: number };
}

export interface PromptResult {
  empty: boolean;
  geometry: BackendGeometry;
  crop?: { start: [number, number, number]; shape: [number, number, number] };
  volumeMl?: number;
  meta: { engine: string; device: string; seconds: number };
}

export interface WorldPoint {
  x: number;
  y: number;
  z: number;
  label: 0 | 1;
}

export interface ExportSegment {
  label: number;
  name: string;
  color: [number, number, number];
  algorithm: 'AUTOMATIC' | 'SEMIAUTOMATIC' | 'MANUAL';
  algorithmName?: string;
  algorithmVersion?: string;
}

export interface ExportResult {
  studyUID: string;
  seriesUID: string;
  sopInstanceUID: string;
  segments: string[];
  frames: number;
}

const API = '/api';

async function check(res: Response): Promise<Response> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = JSON.stringify((await res.json()).detail ?? detail);
    } catch {
      /* not JSON */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await check(res)).json() as Promise<T>;
}

export async function health(): Promise<{ device: string }> {
  return (await check(await fetch(`${API}/health`))).json();
}

export const submitAuto = (studyUID: string, seriesUID: string) =>
  postJson<{ jobId: string }>('/segment/auto', { studyUID, seriesUID });

export const submitPrompt = (
  studyUID: string,
  seriesUID: string,
  points: WorldPoint[],
  box?: { p0: number[]; p1: number[] },
) => postJson<{ jobId: string }>('/segment/prompt', { studyUID, seriesUID, points, box });

export async function waitForJob<R>(jobId: string, onUpdate: (job: Job<R>) => void): Promise<Job<R>> {
  for (;;) {
    const job = (await (await check(await fetch(`${API}/jobs/${jobId}`))).json()) as Job<R>;
    onUpdate(job);
    if (job.status === 'done') return job;
    if (job.status === 'failed') throw new Error(job.error ?? 'job failed');
    await new Promise((r) => setTimeout(r, 750));
  }
}

export async function fetchJobLabelmap(jobId: string): Promise<Uint8Array> {
  const res = await check(await fetch(`${API}/jobs/${jobId}/labelmap`));
  return new Uint8Array(await res.arrayBuffer());
}

export const seriesGeometry = (studyUID: string, seriesUID: string) =>
  postJson<BackendGeometry>('/series/geometry', { studyUID, seriesUID });

export interface SegListItem {
  seriesUID: string;
  sopInstanceUID: string;
  description: string;
  created: string;
}

export async function listSegs(studyUID: string): Promise<SegListItem[]> {
  return (await check(await fetch(`${API}/seg?studyUID=${encodeURIComponent(studyUID)}`))).json();
}

export interface SegLoadResult {
  geometry: BackendGeometry;
  segments: (SegmentDto & { algorithm: string })[];
  sopInstanceUID: string;
}

export const submitSegLoad = (studyUID: string, seriesUID: string, seg: SegListItem) =>
  postJson<{ jobId: string }>('/seg/load', {
    studyUID,
    seriesUID,
    segSeriesUID: seg.seriesUID,
    segSOPInstanceUID: seg.sopInstanceUID,
  });

export async function exportDicomSeg(
  studyUID: string,
  seriesUID: string,
  segments: ExportSegment[],
  labelmap: Uint8Array,
): Promise<ExportResult> {
  const form = new FormData();
  form.append('meta', JSON.stringify({ studyUID, seriesUID, segments }));
  form.append('labelmap', new Blob([labelmap as BlobPart]), 'labelmap.raw');
  const res = await fetch(`${API}/export/dicom-seg`, { method: 'POST', body: form });
  return (await check(res)).json();
}
