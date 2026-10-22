import {
  Enums,
  RenderingEngine,
  cache,
  eventTarget,
  init as coreInit,
  setVolumesForViewports,
  volumeLoader,
  type Types,
} from '@cornerstonejs/core';
import { init as dicomImageLoaderInit } from '@cornerstonejs/dicom-image-loader';
import {
  Enums as ToolEnums,
  PanTool,
  StackScrollTool,
  ToolGroupManager,
  TrackballRotateTool,
  WindowLevelTool,
  ZoomTool,
  addTool,
  init as toolsInit,
} from '@cornerstonejs/tools';

export const RENDERING_ENGINE_ID = 'main-engine';
export const MPR_TOOLGROUP_ID = 'mpr-tools';
export const VR_TOOLGROUP_ID = 'vr-tools';

export const MPR_VIEWPORTS = [
  { id: 'axial', el: 'vp-axial', orientation: Enums.OrientationAxis.AXIAL },
  { id: 'sagittal', el: 'vp-sagittal', orientation: Enums.OrientationAxis.SAGITTAL },
  { id: 'coronal', el: 'vp-coronal', orientation: Enums.OrientationAxis.CORONAL },
] as const;
export const VR_VIEWPORT = { id: 'vr3d', el: 'vp-3d' } as const;

export const MPR_VIEWPORT_IDS: string[] = MPR_VIEWPORTS.map((v) => v.id);

let engine: RenderingEngine | undefined;

export async function initCornerstone(): Promise<void> {
  await coreInit();
  await dicomImageLoaderInit({ maxWebWorkers: Math.max(1, navigator.hardwareConcurrency - 1) });
  await toolsInit();
}

export function createViewer(): RenderingEngine {
  engine = new RenderingEngine(RENDERING_ENGINE_ID);
  const inputs: Types.PublicViewportInput[] = [
    ...MPR_VIEWPORTS.map((v) => ({
      viewportId: v.id,
      type: Enums.ViewportType.ORTHOGRAPHIC,
      element: document.getElementById(v.el) as HTMLDivElement,
      defaultOptions: { orientation: v.orientation, background: [0, 0, 0] as Types.RGB },
    })),
    {
      viewportId: VR_VIEWPORT.id,
      type: Enums.ViewportType.VOLUME_3D,
      element: document.getElementById(VR_VIEWPORT.el) as HTMLDivElement,
      // Coronal camera: patient seen from the front, head up (default looks along the slices).
      defaultOptions: { orientation: Enums.OrientationAxis.CORONAL, background: [0.05, 0.05, 0.08] as Types.RGB },
    },
  ];
  engine.setViewports(inputs);

  for (const t of [WindowLevelTool, PanTool, ZoomTool, StackScrollTool, TrackballRotateTool]) {
    addTool(t);
  }
  const { MouseBindings } = ToolEnums;

  const mpr = ToolGroupManager.createToolGroup(MPR_TOOLGROUP_ID)!;
  for (const name of [WindowLevelTool, PanTool, ZoomTool, StackScrollTool].map((t) => t.toolName)) {
    mpr.addTool(name);
  }
  mpr.setToolActive(WindowLevelTool.toolName, { bindings: [{ mouseButton: MouseBindings.Primary }] });
  mpr.setToolActive(PanTool.toolName, { bindings: [{ mouseButton: MouseBindings.Auxiliary }] });
  mpr.setToolActive(ZoomTool.toolName, { bindings: [{ mouseButton: MouseBindings.Secondary }] });
  mpr.setToolActive(StackScrollTool.toolName, { bindings: [{ mouseButton: MouseBindings.Wheel }] });
  for (const v of MPR_VIEWPORTS) mpr.addViewport(v.id, RENDERING_ENGINE_ID);

  const vr = ToolGroupManager.createToolGroup(VR_TOOLGROUP_ID)!;
  vr.addTool(TrackballRotateTool.toolName);
  vr.addTool(ZoomTool.toolName);
  vr.addTool(PanTool.toolName);
  vr.setToolActive(TrackballRotateTool.toolName, { bindings: [{ mouseButton: MouseBindings.Primary }] });
  vr.setToolActive(PanTool.toolName, { bindings: [{ mouseButton: MouseBindings.Auxiliary }] });
  vr.setToolActive(ZoomTool.toolName, { bindings: [{ mouseButton: MouseBindings.Secondary }] });
  vr.addViewport(VR_VIEWPORT.id, RENDERING_ENGINE_ID);

  // Stop the browser context menu stealing right-drag (zoom).
  document.getElementById('viewports')!.addEventListener('contextmenu', (e) => e.preventDefault());
  return engine;
}

export function getEngine(): RenderingEngine {
  if (!engine) throw new Error('viewer not created');
  return engine;
}

let currentVolumeId: string | undefined;

/** Loads a CT series as a streaming volume into all viewports; resolves when fully loaded. */
export async function loadVolume(
  seriesUID: string,
  imageIds: string[],
  onProgress?: (loaded: number, total: number) => void,
): Promise<Types.IImageVolume> {
  if (currentVolumeId) cache.removeVolumeLoadObject(currentVolumeId);
  const volumeId = `cornerstoneStreamingImageVolume:${seriesUID}`;
  currentVolumeId = volumeId;
  const volume = await volumeLoader.createAndCacheVolume(volumeId, { imageIds });

  let loaded = 0;
  const onModified = (evt: Event) => {
    const detail = (evt as CustomEvent).detail as { volumeId?: string };
    if (detail?.volumeId !== volumeId) return;
    loaded += 1;
    onProgress?.(Math.min(loaded, imageIds.length), imageIds.length);
  };
  eventTarget.addEventListener(Enums.Events.IMAGE_VOLUME_MODIFIED, onModified);
  const done = new Promise<void>((resolve) => volume.load(() => resolve())).finally(() =>
    eventTarget.removeEventListener(Enums.Events.IMAGE_VOLUME_MODIFIED, onModified),
  );

  const eng = getEngine();
  await setVolumesForViewports(
    eng,
    [{ volumeId, callback: ({ volumeActor }) => setCtWindow(volumeActor) }],
    MPR_VIEWPORT_IDS,
  );
  await setVolumesForViewports(eng, [{ volumeId }], [VR_VIEWPORT.id]);
  const vr = eng.getViewport(VR_VIEWPORT.id) as Types.IVolumeViewport;
  vr.setProperties({ preset: 'CT-Chest-Contrast-Enhanced' }); // portal-venous contrast CT
  eng.renderViewports([...MPR_VIEWPORT_IDS, VR_VIEWPORT.id]);

  await done;
  vr.resetCamera();
  vr.render();
  return volume;
}

/** Soft-tissue window (W 400 / L 40). */
function setCtWindow(actor: Types.VolumeActor): void {
  actor.getProperty().getRGBTransferFunction(0).setMappingRange(-160, 240);
}
