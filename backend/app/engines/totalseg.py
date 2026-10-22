"""TotalSegmentator (`total` task, Apache-2.0 weights) as an automatic engine."""

from __future__ import annotations

import os
import time

import numpy as np
import SimpleITK as sitk

from app.config import settings, torch_device
from app.engines.base import AutoResult, Progress
from app.labels import totalseg_catalogue
from app.volume import Geometry, nifti_to_sitk, resample_labels_to, sitk_to_nifti


def _prepare_totalseg_home() -> None:
    """Keep weights inside the repo and make sure usage statistics are never sent."""
    home = settings().checkpoint_dir / "totalseg"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["TOTALSEG_HOME_DIR"] = str(home)
    from totalsegmentator.config import set_config_key, setup_totalseg

    setup_totalseg()
    set_config_key("send_usage_stats", False)


class TotalSegmentatorEngine:
    name = "totalsegmentator"

    def __init__(self, fast: bool | None = None) -> None:
        self.device = torch_device()
        # Full 1.5 mm model on GPU; the 3 mm model on CPU (full-res on CPU takes many minutes).
        self.fast = (self.device == "cpu") if fast is None else fast
        _prepare_totalseg_home()

    def segment(self, image: sitk.Image, progress: Progress) -> AutoResult:
        from totalsegmentator.python_api import totalsegmentator

        mode = "fast 3 mm" if self.fast else "full 1.5 mm"
        progress(f"TotalSegmentator ({mode}, {self.device}) running…")
        t0 = time.perf_counter()
        out = totalsegmentator(
            sitk_to_nifti(image),
            None,
            fast=self.fast,
            device="gpu" if self.device == "cuda" else "cpu",
            quiet=True,
        )
        elapsed = time.perf_counter() - t0
        labels = resample_labels_to(nifti_to_sitk(out), image)
        if not Geometry.of(labels).matches(Geometry.of(image)):
            raise RuntimeError("label map geometry does not match the source CT")
        labels = sitk.Cast(labels, sitk.sitkUInt8)
        present = np.unique(sitk.GetArrayViewFromImage(labels))
        catalogue = totalseg_catalogue("total")
        segments = {int(v): catalogue[int(v)] for v in present if v != 0 and int(v) in catalogue}
        return AutoResult(
            labels=labels,
            segments=segments,
            meta={"engine": self.name, "mode": mode, "device": self.device, "seconds": elapsed},
        )
