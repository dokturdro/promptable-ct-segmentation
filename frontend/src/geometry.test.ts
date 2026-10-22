import { describe, expect, it } from 'vitest';

import { gather, mapBackendToVolume, scatter, type BackendGeometry } from './geometry';

// Backend grid: 4 x 3 x 2 (i, j, k), rows pointing anterior and slices feet-wards,
// like the Pancreas-CT headers.
const be: BackendGeometry = {
  size: [4, 3, 2],
  spacing: [0.8, 0.7, 2.5],
  origin: [10, 20, 30],
  direction: [1, 0, 0, 0, -1, 0, 0, 0, -1], // columns = i, j, k axes
};

// Backend array (k, j, i) with a unique value per voxel: v = 1 + i + 4j + 12k.
const src = Uint8Array.from({ length: 24 }, (_, p) => p + 1);
const beValue = (i: number, j: number, k: number) => 1 + i + 4 * j + 12 * k;

function world(g: { origin: ArrayLike<number>; spacing: ArrayLike<number> }, axes: number[][], idx: number[]) {
  return [0, 1, 2].map((x) => g.origin[x] + idx.reduce((s, v, a) => s + v * g.spacing[a] * axes[a][x], 0));
}

function checkByWorld(vol: {
  dimensions: number[];
  spacing: number[];
  origin: number[];
  direction: number[];
}) {
  const m = mapBackendToVolume(be, vol);
  const out = new Uint8Array(24);
  scatter(m, src, (d, v) => (out[d] = v));
  const beAxes = [0, 1, 2].map((a) => [be.direction[a], be.direction[3 + a], be.direction[6 + a]]);
  const csAxes = [0, 1, 2].map((b) => vol.direction.slice(3 * b, 3 * b + 3));
  const n = vol.dimensions;
  // Every Cornerstone voxel must hold the backend value of the voxel at the same world point.
  for (let c2 = 0; c2 < n[2]; c2++)
    for (let c1 = 0; c1 < n[1]; c1++)
      for (let c0 = 0; c0 < n[0]; c0++) {
        const w = world(vol, csAxes, [c0, c1, c2]);
        // invert backend affine (orthonormal axes): idx_a = dot(w - origin, axis_a) / spacing_a
        const ijk = [0, 1, 2].map((a) =>
          Math.round(beAxes[a].reduce((s, d, x) => s + d * (w[x] - be.origin[x]), 0) / be.spacing[a]),
        );
        expect(out[c0 + c1 * n[0] + c2 * n[0] * n[1]]).toBe(beValue(ijk[0], ijk[1], ijk[2]));
      }
  // gather is the exact inverse of scatter
  expect(Array.from(gather(m, out))).toEqual(Array.from(src));
  return m;
}

describe('mapBackendToVolume', () => {
  it('is the identity when both grids are described the same way', () => {
    const m = checkByWorld({ dimensions: [4, 3, 2], spacing: [0.8, 0.7, 2.5], origin: [10, 20, 30], direction: be.direction });
    expect(m.identity).toBe(true);
  });

  it('handles a volume whose slices are sorted the other way (k flipped)', () => {
    const m = checkByWorld({
      dimensions: [4, 3, 2],
      spacing: [0.8, 0.7, 2.5],
      origin: [10, 20, 30 - 2.5], // last backend slice comes first
      direction: [1, 0, 0, 0, -1, 0, 0, 0, 1],
    });
    expect(m.identity).toBe(false);
  });

  it('handles permuted and flipped in-plane axes', () => {
    checkByWorld({
      dimensions: [3, 4, 2], // cs axis 0 = backend j (flipped), cs axis 1 = backend i
      spacing: [0.7, 0.8, 2.5],
      origin: [10, 20 - 2 * 0.7, 30],
      direction: [0, 1, 0, 1, 0, 0, 0, 0, -1],
    });
  });

  it('refuses grids that do not coincide', () => {
    const shifted = { dimensions: [4, 3, 2], spacing: [0.8, 0.7, 2.5], origin: [10.5, 20, 30], direction: be.direction };
    expect(() => mapBackendToVolume(be, shifted)).toThrow(/offset/);
    const resized = { dimensions: [4, 3, 3], spacing: [0.8, 0.7, 2.5], origin: [10, 20, 30], direction: be.direction };
    expect(() => mapBackendToVolume(be, resized)).toThrow(/size/);
  });

  it('scatters a crop to the right place', () => {
    const vol = { dimensions: [4, 3, 2], spacing: [0.8, 0.7, 2.5], origin: [10, 20, 27.5], direction: [1, 0, 0, 0, -1, 0, 0, 0, 1] };
    const m = mapBackendToVolume(be, vol);
    const out = new Uint8Array(24);
    // 1x1x2 crop at backend (k=1, j=2, i=1..2)
    scatter(m, Uint8Array.from([7, 9]), (d, v) => (out[d] = v), [1, 2, 1], [1, 1, 2]);
    // backend k=1 is cs k=0 (flipped); j and i unchanged
    expect(out[1 + 2 * 4 + 0]).toBe(7);
    expect(out[2 + 2 * 4 + 0]).toBe(9);
    expect(out.reduce((a, b) => a + (b ? 1 : 0), 0)).toBe(2);
  });
});
