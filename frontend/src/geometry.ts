/**
 * Voxel-order mapping between the backend's label arrays and Cornerstone's labelmap volume.
 *
 * Backend (SimpleITK): C-order (k, j, i), i fastest; `direction` is row-major 3x3 whose
 * COLUMNS are the i/j/k axis directions (LPS).
 * Cornerstone volume: linear index = c0 + c1*n0 + c2*n0*n1; `direction` is 9 numbers whose
 * consecutive TRIPLETS are the axis directions.
 *
 * Both should describe the same physical grid, but axis order/sign can differ (e.g. slice
 * sorting). We match axes by direction, verify the grids coincide in world space, and
 * refuse to map otherwise: a silently misaligned mask is worse than an error.
 */

export interface BackendGeometry {
  size: [number, number, number];
  spacing: [number, number, number];
  origin: [number, number, number];
  direction: number[];
}

export interface VolumeGeometry {
  dimensions: ArrayLike<number>;
  spacing: ArrayLike<number>;
  origin: ArrayLike<number>;
  direction: ArrayLike<number>;
}

export interface AxisMapping {
  /** Per backend axis (i, j, k): signed step in the Cornerstone linear index. */
  step: [number, number, number];
  /** Cornerstone linear index of backend voxel (0, 0, 0). */
  offset: number;
  size: [number, number, number];
  identity: boolean;
}

const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

export function mapBackendToVolume(be: BackendGeometry, cs: VolumeGeometry, tolMm = 0.05): AxisMapping {
  const beAxes = [0, 1, 2].map((a) => [be.direction[a], be.direction[3 + a], be.direction[6 + a]]);
  const csAxes = [0, 1, 2].map((b) => [cs.direction[3 * b], cs.direction[3 * b + 1], cs.direction[3 * b + 2]]);
  const n = [cs.dimensions[0], cs.dimensions[1], cs.dimensions[2]];
  const stride = [1, n[0], n[0] * n[1]];

  const step: number[] = [];
  let offset = 0;
  const used = new Set<number>();
  for (let a = 0; a < 3; a++) {
    const b = [0, 1, 2].find((c) => !used.has(c) && Math.abs(Math.abs(dot(beAxes[a], csAxes[c])) - 1) < 1e-3);
    if (b === undefined) throw new Error(`backend axis ${a} has no matching volume axis`);
    used.add(b);
    if (n[b] !== be.size[a]) throw new Error(`axis ${a}: size ${be.size[a]} vs volume ${n[b]}`);
    if (Math.abs(cs.spacing[b] - be.spacing[a]) > 1e-3) {
      throw new Error(`axis ${a}: spacing ${be.spacing[a]} vs volume ${cs.spacing[b]}`);
    }
    const sign = dot(beAxes[a], csAxes[b]) > 0 ? 1 : -1;
    step.push(sign * stride[b]);
    if (sign < 0) offset += (n[b] - 1) * stride[b];
  }

  // Backend voxel (0,0,0) must sit at the same world point in both grids.
  const csIdx = [0, 1, 2].map((b) => Math.floor(offset / stride[b]) % n[b]);
  const csWorld = [0, 1, 2].map(
    (x) => cs.origin[x] + csIdx.reduce((s, ib, b) => s + ib * cs.spacing[b] * csAxes[b][x], 0),
  );
  const err = Math.hypot(...csWorld.map((v, x) => v - be.origin[x]));
  if (err > tolMm) throw new Error(`grids are offset by ${err.toFixed(3)} mm`);

  const size = [be.size[0], be.size[1], be.size[2]] as [number, number, number];
  const identity = offset === 0 && step[0] === 1 && step[1] === n[0] && step[2] === n[0] * n[1];
  return { step: step as [number, number, number], offset, size, identity };
}

/** Linear Cornerstone index of backend voxel (i, j, k). */
export const volumeIndex = (m: AxisMapping, i: number, j: number, k: number) =>
  m.offset + i * m.step[0] + j * m.step[1] + k * m.step[2];

/**
 * Writes a backend (k, j, i) block into `target`. `start`/`shape` (k, j, i) describe a crop of
 * the backend grid (defaults: the full grid). `write(dstIndex, value)` lets callers merge.
 */
export function scatter(
  m: AxisMapping,
  src: Uint8Array,
  write: (dst: number, value: number) => void,
  start: [number, number, number] = [0, 0, 0],
  shape: [number, number, number] = [m.size[2], m.size[1], m.size[0]],
): void {
  const [sk, sj, si] = shape;
  let p = 0;
  for (let k = 0; k < sk; k++) {
    for (let j = 0; j < sj; j++) {
      let dst = volumeIndex(m, start[2], start[1] + j, start[0] + k);
      for (let i = 0; i < si; i++, p++, dst += m.step[0]) write(dst, src[p]);
    }
  }
}

/** Reads the full grid out of a Cornerstone-ordered array into backend (k, j, i) order. */
export function gather(m: AxisMapping, src: ArrayLike<number>): Uint8Array {
  const [ni, nj, nk] = m.size;
  const out = new Uint8Array(ni * nj * nk);
  if (m.identity) {
    for (let p = 0; p < out.length; p++) out[p] = src[p];
    return out;
  }
  let p = 0;
  for (let k = 0; k < nk; k++) {
    for (let j = 0; j < nj; j++) {
      let s = volumeIndex(m, 0, j, k);
      for (let i = 0; i < ni; i++, p++, s += m.step[0]) out[p] = src[s];
    }
  }
  return out;
}
