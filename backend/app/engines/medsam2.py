"""MedSAM2 (SAM2.1-tiny fine-tuned on 3D medical data) as a click-prompted 3D engine.

The CT is treated as a "video" of axial slices: clicks are added on their slice(s), and the
mask is propagated through neighbouring slices in both directions.

Weights: MedSAM2_latest.pt, CC-BY-SA-4.0, research and education use only.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import SimpleITK as sitk
import torch
import torch.nn.functional as F
from scipy import ndimage

from app.config import settings, torch_device
from app.engines.base import BoxPrompt, PointPrompt, Progress, PromptResult

CONFIG_DIR = Path(__file__).parent / "medsam2_configs"
CONFIG_NAME = "sam2.1_hiera_t512.yaml"
IMAGE_SIZE = 512
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def _build_predictor(checkpoint: Path, device: str):
    import sam2  # noqa: F401  (registers its own hydra config module on import)
    from hydra import initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra
    from sam2.build_sam import build_sam2_video_predictor_npz

    # The installed package lacks its YAML configs; point hydra at our vendored copy.
    GlobalHydra.instance().clear()
    initialize_config_dir(config_dir=str(CONFIG_DIR), version_base="1.2")
    return build_sam2_video_predictor_npz(CONFIG_NAME, str(checkpoint), device=device)


class MedSAM2Engine:
    name = "medsam2"

    def __init__(
        self,
        checkpoint: Path | None = None,
        window: tuple[float, float] = (-160.0, 240.0),  # soft tissue W400/L40
        max_slices_each_way: int | None = None,
    ) -> None:
        self.device = torch_device()
        self.checkpoint = checkpoint or settings().checkpoint_dir / "medsam2" / "MedSAM2_latest.pt"
        if not self.checkpoint.exists():
            raise FileNotFoundError(f"{self.checkpoint} missing; run scripts/download_weights.py")
        self.window = window
        # CPU is ~100x slower per slice: bound the propagation range there.
        self.max_slices = max_slices_each_way or (400 if self.device == "cuda" else 25)
        self._predictor = _build_predictor(self.checkpoint, self.device)
        self._lock = threading.Lock()

    def _preprocess(self, vol: np.ndarray) -> torch.Tensor:
        """(d, h, w) HU -> (d, 3, 512, 512) normalised float tensor on device."""
        lo, hi = self.window
        x = torch.from_numpy(np.clip((vol.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0))
        x = x.to(self.device)[:, None]  # (d, 1, h, w)
        # Bilinear resize (the reference script uses PIL bilinear on uint8 slices).
        x = F.interpolate(x, size=(IMAGE_SIZE, IMAGE_SIZE), mode="bilinear", align_corners=False)
        x = x.expand(-1, 3, -1, -1)
        mean = torch.tensor(IMAGENET_MEAN, device=self.device)[None, :, None, None]
        std = torch.tensor(IMAGENET_STD, device=self.device)[None, :, None, None]
        return (x - mean) / std

    def segment_prompt(
        self,
        image: sitk.Image,
        points: list[PointPrompt],
        progress: Progress,
        boxes: list[BoxPrompt] | None = None,
        text: str | None = None,
    ) -> PromptResult:
        boxes = boxes or []
        if not boxes and not any(p.label == 1 for p in points):
            raise ValueError("need a box or at least one positive (include) point")
        vol = sitk.GetArrayViewFromImage(image)  # (k, j, i)
        depth, height, width = vol.shape
        ks = [p.k for p in points] + [b.k for b in boxes]
        k0 = max(0, min(ks) - self.max_slices)
        k1 = min(depth, max(ks) + self.max_slices + 1)

        t0 = time.perf_counter()
        progress(f"MedSAM2: encoding {k1 - k0} slices on {self.device}…")
        pts_by_frame: dict[int, list[PointPrompt]] = defaultdict(list)
        for p in points:
            pts_by_frame[p.k - k0].append(p)
        box_by_frame = {b.k - k0: b for b in boxes}  # SAM2 takes one box per frame
        frames_with_prompts = sorted(set(pts_by_frame) | set(box_by_frame))

        mask = np.zeros((k1 - k0, height, width), dtype=bool)
        autocast = (
            torch.autocast("cuda", dtype=torch.bfloat16)
            if self.device == "cuda"
            else torch.autocast("cpu", enabled=False)
        )
        with self._lock, torch.inference_mode(), autocast:
            frames = self._preprocess(np.asarray(vol[k0:k1]))
            state = self._predictor.init_state(frames, height, width)
            for reverse in (False, True):
                for f in frames_with_prompts:
                    pts, box = pts_by_frame.get(f, []), box_by_frame.get(f)
                    self._predictor.add_new_points_or_box(
                        inference_state=state,
                        frame_idx=f,
                        obj_id=1,
                        points=np.array([[p.i, p.j] for p in pts], np.float32) if pts else None,
                        labels=np.array([p.label for p in pts], np.int32) if pts else None,
                        box=np.array([box.i0, box.j0, box.i1, box.j1], np.float32) if box else None,
                    )
                progress(f"MedSAM2: propagating {'down' if reverse else 'up'}…")
                for f, _, logits in self._predictor.propagate_in_video(state, reverse=reverse):
                    mask[f] |= (logits[0, 0] > 0.0).cpu().numpy()
                self._predictor.reset_state(state)
            del frames, state
        if self.device == "cuda":
            torch.cuda.empty_cache()

        # Box centres count as positive anchors for the connected-component filter.
        anchors = points + [
            PointPrompt((b.i0 + b.i1) // 2, (b.j0 + b.j1) // 2, b.k, 1) for b in boxes
        ]
        mask = _keep_components_with_positive_clicks(mask, anchors, k0)
        full = np.zeros((depth, height, width), dtype=np.uint8)
        full[k0:k1] = mask
        out = sitk.GetImageFromArray(full)
        out.CopyInformation(image)
        return PromptResult(
            mask=out,
            meta={
                "engine": self.name,
                "device": self.device,
                "seconds": time.perf_counter() - t0,
                "slices": [k0, k1],
                "window": list(self.window),
            },
        )


def _keep_components_with_positive_clicks(
    mask: np.ndarray, points: list[PointPrompt], k0: int
) -> np.ndarray:
    labels, n = ndimage.label(mask)
    if n <= 1:
        return mask
    keep = {
        int(labels[p.k - k0, p.j, p.i])
        for p in points
        if p.label == 1 and labels[p.k - k0, p.j, p.i]
    }
    if not keep:  # clicked outside the predicted mask: fall back to the largest component
        keep = {int(np.argmax(np.bincount(labels.ravel())[1:]) + 1)}
    return np.isin(labels, list(keep))
