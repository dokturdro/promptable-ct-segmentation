import {
  BrushTool,
  Enums as ToolEnums,
  ToolGroupManager,
  WindowLevelTool,
  addTool,
  segmentation,
  utilities as toolUtils,
} from '@cornerstonejs/tools';

import { MPR_TOOLGROUP_ID } from './viewer';

export type Mode = 'navigate' | 'prompt' | 'brush' | 'eraser';

const BRUSH = 'CircularBrush';
const ERASER = 'CircularEraser';
const { MouseBindings } = ToolEnums;

let mode: Mode = 'navigate';

export function initEditTools(): void {
  addTool(BrushTool);
  const tg = ToolGroupManager.getToolGroup(MPR_TOOLGROUP_ID)!;
  tg.addToolInstance(BRUSH, BrushTool.toolName, { activeStrategy: 'FILL_INSIDE_CIRCLE' });
  tg.addToolInstance(ERASER, BrushTool.toolName, { activeStrategy: 'ERASE_INSIDE_CIRCLE' });
  tg.setToolPassive(BRUSH);
  tg.setToolPassive(ERASER);
}

export function getMode(): Mode {
  return mode;
}

/** Binds the primary mouse button. In 'prompt' mode it is left free for the click handler. */
export function setMode(next: Mode): void {
  const tg = ToolGroupManager.getToolGroup(MPR_TOOLGROUP_ID)!;
  for (const name of [WindowLevelTool.toolName, BRUSH, ERASER]) tg.setToolPassive(name);
  const primary = { bindings: [{ mouseButton: MouseBindings.Primary }] };
  if (next === 'navigate') tg.setToolActive(WindowLevelTool.toolName, primary);
  if (next === 'brush') tg.setToolActive(BRUSH, primary);
  if (next === 'eraser') tg.setToolActive(ERASER, primary);
  mode = next;
}

export function setBrushSize(mm: number): void {
  toolUtils.segmentation.setBrushSizeForToolGroup(MPR_TOOLGROUP_ID, mm);
}

export function setActiveSegment(segmentationId: string, label: number): void {
  segmentation.segmentIndex.setActiveSegmentIndex(segmentationId, label);
}
