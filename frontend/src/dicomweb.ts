import { wadors } from '@cornerstonejs/dicom-image-loader';

/** DICOMweb root as seen by the browser (proxied to Orthanc by Vite). */
export const WADO_ROOT = `${window.location.origin}/dicom-web`;

type DicomJson = Record<string, { vr: string; Value?: unknown[] }>;

const TAG = {
  StudyInstanceUID: '0020000D',
  SeriesInstanceUID: '0020000E',
  SOPInstanceUID: '00080018',
  Modality: '00080060',
  SeriesDescription: '0008103E',
  PatientID: '00100020',
  NumberOfSeriesRelatedInstances: '00201209',
} as const;

function str(ds: DicomJson, tag: string): string {
  const v = ds[tag]?.Value?.[0];
  return v === undefined ? '' : String(v);
}

export interface SeriesInfo {
  studyUID: string;
  seriesUID: string;
  modality: string;
  description: string;
  patientId: string;
  numInstances: number;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: 'application/dicom+json' } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url}`);
  // Orthanc returns 204/empty body for empty results.
  const text = await res.text();
  return (text ? JSON.parse(text) : []) as T;
}

export async function listSeries(): Promise<SeriesInfo[]> {
  const fields = ['SeriesDescription', 'PatientID', 'NumberOfSeriesRelatedInstances'];
  const rows = await getJson<DicomJson[]>(
    `${WADO_ROOT}/series?${fields.map((f) => `includefield=${f}`).join('&')}`,
  );
  return rows.map((r) => ({
    studyUID: str(r, TAG.StudyInstanceUID),
    seriesUID: str(r, TAG.SeriesInstanceUID),
    modality: str(r, TAG.Modality),
    description: str(r, TAG.SeriesDescription),
    patientId: str(r, TAG.PatientID),
    numInstances: Number(str(r, TAG.NumberOfSeriesRelatedInstances) || 0),
  }));
}

/** Fetches series metadata, registers it with the WADO-RS loader and returns image ids. */
export async function createImageIds(studyUID: string, seriesUID: string): Promise<string[]> {
  const instances = await getJson<DicomJson[]>(
    `${WADO_ROOT}/studies/${studyUID}/series/${seriesUID}/metadata`,
  );
  return instances.map((inst) => {
    const sop = str(inst, TAG.SOPInstanceUID);
    const imageId =
      `wadors:${WADO_ROOT}/studies/${studyUID}/series/${seriesUID}` +
      `/instances/${sop}/frames/1`;
    wadors.metaDataManager.add(imageId, inst as never);
    return imageId;
  });
}
