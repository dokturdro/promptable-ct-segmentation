"""Simulated-prompt evaluation of the prompt engine on the seeded CT.

Simulated user prompts per organ:
  click1    1 positive click at the most interior point of the organ's largest slice
  click3    1 click on each of 3 slices at 25/50/75% of the organ's extent
  box       tight box (+5 px margin) on the largest slice (MedSAM2's training-style prompt)
  box+click box and interior click on the same slice

References: the expert pancreas SEG from IDC; TotalSegmentator (full res) for other organs.

Usage: uv run --project backend python scripts/eval_prompt.py [--modes click1 box]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("TQDM_DISABLE", "1")

import highdicom as hd  # noqa: E402
import numpy as np  # noqa: E402
import SimpleITK as sitk  # noqa: E402
from scipy import ndimage  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.engines.base import BoxPrompt, PointPrompt  # noqa: E402
from app.engines.medsam2 import MedSAM2Engine  # noqa: E402
from app.engines.totalseg import TotalSegmentatorEngine  # noqa: E402
from app.volume import image_from_affine, read_dicom_series, resample_labels_to  # noqa: E402

ORGANS = {"spleen": 1, "kidney_left": 3, "liver": 5, "pancreas": 7}
MODES = ["click1", "click3", "box", "box+click"]


def interior_click(mask_slice: np.ndarray, k: int) -> PointPrompt:
    dist = ndimage.distance_transform_edt(mask_slice)
    j, i = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return PointPrompt(i=int(i), j=int(j), k=k, label=1)


def tight_box(mask_slice: np.ndarray, k: int, margin: int = 5) -> BoxPrompt:
    js, is_ = np.nonzero(mask_slice)
    h, w = mask_slice.shape
    return BoxPrompt(
        max(0, is_.min() - margin),
        max(0, js.min() - margin),
        min(w - 1, is_.max() + margin),
        min(h - 1, js.max() + margin),
        k,
    )


def prompts_for(mask: np.ndarray, mode: str) -> tuple[list[PointPrompt], list[BoxPrompt]]:
    areas = mask.sum(axis=(1, 2))
    k = int(np.argmax(areas))
    if mode == "click1":
        return [interior_click(mask[k], k)], []
    if mode == "click3":
        ks = np.flatnonzero(areas)
        picks = [int(ks[int(q * (len(ks) - 1))]) for q in (0.25, 0.5, 0.75)]
        return [interior_click(mask[p], p) for p in picks], []
    if mode == "box":
        return [], [tight_box(mask[k], k)]
    if mode == "box+click":
        return [interior_click(mask[k], k)], [tight_box(mask[k], k)]
    raise ValueError(mode)


def dice(a: np.ndarray, b: np.ndarray) -> float:
    return float(2 * (a & b).sum() / max(1, a.sum() + b.sum()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", default="PANCREAS_0001")
    ap.add_argument("--modes", nargs="+", default=MODES, choices=MODES)
    args = ap.parse_args()

    raw = REPO / "data" / "raw" / args.patient
    img, _ = read_dicom_series(next(raw.glob("CT_*")))
    cached = REPO / "data" / "totalseg_fast0.nii.gz"
    if cached.exists():
        ts = sitk.GetArrayFromImage(sitk.ReadImage(str(cached)))
    else:
        ts = sitk.GetArrayFromImage(TotalSegmentatorEngine(fast=False).segment(img, print).labels)
    refs = {name: ts == lbl for name, lbl in ORGANS.items()}
    seg_files = list(raw.glob("SEG_*/*.dcm"))
    if seg_files:
        vol = hd.seg.segread(seg_files[0]).get_volume(combine_segments=True)
        ref = image_from_affine(vol.array.astype(np.uint8), vol.affine)
        refs["pancreas"] = sitk.GetArrayFromImage(resample_labels_to(ref, img)) > 0

    engine = MedSAM2Engine()
    vox_ml = float(np.prod(img.GetSpacing())) / 1000
    print(f"{'organ':12} {'prompt':>10} {'Dice':>6} {'pred mL':>8} {'ref mL':>7} {'sec':>5}")
    for name, ref in refs.items():
        if not ref.any():
            continue
        src = "expert" if name == "pancreas" and seg_files else "TotalSeg"
        for mode in args.modes:
            pts, boxes = prompts_for(ref, mode)
            t = time.perf_counter()
            pred = engine.segment_prompt(img, pts, lambda _m: None, boxes=boxes).mask
            p = sitk.GetArrayFromImage(pred) > 0
            print(
                f"{name:12} {mode:>10} {dice(p, ref):6.3f} {p.sum() * vox_ml:8.0f} "
                f"{ref.sum() * vox_ml:7.0f} {time.perf_counter() - t:5.1f}  (ref: {src})",
                flush=True,
            )


if __name__ == "__main__":
    main()
