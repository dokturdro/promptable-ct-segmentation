import { Enums, type Types } from '@cornerstonejs/core';

import type { WorldPoint } from './api';
import { getMode } from './tools';
import { MPR_VIEWPORTS, getEngine } from './viewer';

export interface PromptState {
  points: WorldPoint[];
  box?: { p0: number[]; p1: number[] };
}

const state: PromptState = { points: [] };
let onChange: (s: PromptState) => void = () => {};
const overlays = new Map<string, SVGSVGElement>();
const SVG_NS = 'http://www.w3.org/2000/svg';

export function getPromptState(): PromptState {
  return state;
}

export function clearPrompts(): void {
  state.points = [];
  state.box = undefined;
  redrawAll();
}

function viewportFor(id: string): Types.IVolumeViewport {
  return getEngine().getViewport(id) as Types.IVolumeViewport;
}

function toWorld(vp: Types.IVolumeViewport, el: HTMLElement, evt: MouseEvent): number[] {
  const r = el.getBoundingClientRect();
  return Array.from(vp.canvasToWorld([evt.clientX - r.left, evt.clientY - r.top]));
}

/** Wires click/box prompting on the MPR viewports. `changed` fires after each new prompt. */
export function initPromptInteraction(changed: (s: PromptState) => void): void {
  onChange = changed;
  for (const v of MPR_VIEWPORTS) {
    const el = document.getElementById(v.el)!;
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.classList.add('prompt-overlay');
    el.append(svg);
    overlays.set(v.id, svg);
    el.addEventListener(Enums.Events.IMAGE_RENDERED, () => redraw(v.id));

    let boxStart: number[] | undefined;
    el.addEventListener('mousedown', (evt) => {
      if (getMode() !== 'prompt' || evt.button !== 0) return;
      const vp = viewportFor(v.id);
      if (evt.ctrlKey && v.id === 'axial') {
        boxStart = toWorld(vp, el, evt);
        return;
      }
      const [x, y, z] = toWorld(vp, el, evt);
      state.points.push({ x, y, z, label: evt.shiftKey ? 0 : 1 });
      redrawAll();
      onChange(state);
    });
    el.addEventListener('mouseup', (evt) => {
      if (!boxStart || getMode() !== 'prompt') return;
      const end = toWorld(viewportFor(v.id), el, evt);
      // Both corners lie in the displayed axial plane; reuse the start z for robustness.
      state.box = { p0: boxStart, p1: [end[0], end[1], boxStart[2]] };
      boxStart = undefined;
      redrawAll();
      onChange(state);
    });
  }
}

function redrawAll(): void {
  for (const v of MPR_VIEWPORTS) redraw(v.id);
}

function redraw(viewportId: string): void {
  const svg = overlays.get(viewportId);
  if (!svg) return;
  svg.replaceChildren();
  const vp = viewportFor(viewportId);
  const cam = vp.getCamera();
  if (!cam.focalPoint || !cam.viewPlaneNormal) return;
  const n = cam.viewPlaneNormal;
  const f = cam.focalPoint;
  const distToPlane = (p: number[]) =>
    Math.abs((p[0] - f[0]) * n[0] + (p[1] - f[1]) * n[1] + (p[2] - f[2]) * n[2]);

  for (const p of state.points) {
    const d = distToPlane([p.x, p.y, p.z]);
    if (d > 15) continue; // only near the displayed slice
    const [cx, cy] = vp.worldToCanvas([p.x, p.y, p.z]);
    const c = document.createElementNS(SVG_NS, 'circle');
    c.setAttribute('cx', String(cx));
    c.setAttribute('cy', String(cy));
    c.setAttribute('r', '5');
    c.setAttribute('class', p.label ? 'pt-include' : 'pt-exclude');
    c.setAttribute('opacity', String(d < 1 ? 1 : 0.35));
    svg.append(c);
  }
  if (state.box && distToPlane(state.box.p0) < 1) {
    const a = vp.worldToCanvas(state.box.p0 as Types.Point3);
    const b = vp.worldToCanvas(state.box.p1 as Types.Point3);
    const r = document.createElementNS(SVG_NS, 'rect');
    r.setAttribute('x', String(Math.min(a[0], b[0])));
    r.setAttribute('y', String(Math.min(a[1], b[1])));
    r.setAttribute('width', String(Math.abs(a[0] - b[0])));
    r.setAttribute('height', String(Math.abs(a[1] - b[1])));
    r.setAttribute('class', 'prompt-box');
    svg.append(r);
  }
}
