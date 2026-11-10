from __future__ import annotations

import os
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings:
    orthanc_url: str = os.environ.get("ORTHANC_URL", "http://127.0.0.1:8042")
    data_dir: Path = Path(os.environ.get("DATA_DIR", REPO_ROOT / "data"))
    checkpoint_dir: Path = Path(os.environ.get("CHECKPOINT_DIR", REPO_ROOT / "checkpoints"))
    # "auto" picks CUDA when available, else CPU (TotalSegmentator then forces --fast).
    device: str = os.environ.get("SEG_DEVICE", "auto")

    @property
    def dicomweb_url(self) -> str:
        return f"{self.orthanc_url}/dicom-web"

    @property
    def series_cache(self) -> Path:
        return self.data_dir / "cache" / "series"

    @property
    def raw_dir(self) -> Path:
        """Seed data downloaded from IDC, one folder per patient."""
        return self.data_dir / "raw"

    @property
    def medsam2_checkpoint(self) -> Path:
        return self.checkpoint_dir / "medsam2" / "MedSAM2_latest.pt"

    @property
    def totalseg_home(self) -> Path:
        return self.checkpoint_dir / "totalseg"


@cache
def settings() -> Settings:
    return Settings()


@cache
def torch_device() -> str:
    import torch

    want = settings().device
    if want == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return want
