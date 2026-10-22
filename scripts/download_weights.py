"""Download model weights into ./checkpoints (gitignored).

  MedSAM2_latest.pt         148.8 MB  huggingface.co/wanglab/MedSAM2
                                      CC-BY-SA-4.0, research/education use only
  TotalSegmentator `total`  1.24 GB   github.com/wasserth/TotalSegmentator (v2.0.0-weights)
                                      Apache-2.0; 5 x 223 MB full-res + 129 MB 3 mm "fast"

Usage: uv run --project backend python scripts/download_weights.py [--skip-totalseg]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
CKPT = REPO / "checkpoints"
MEDSAM2_URL = "https://huggingface.co/wanglab/MedSAM2/resolve/main/MedSAM2_latest.pt"
MEDSAM2_SHA256 = "c92743b99f00d078bf32a3afcc38aaa9faf1c1692dffe3eaa7a90938c1991060"
TOTALSEG_TASK_IDS = [291, 292, 293, 294, 295, 297]  # `total` full res (5 parts) + 3 mm fast


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_medsam2() -> None:
    dest = CKPT / "medsam2" / "MedSAM2_latest.pt"
    if dest.exists() and sha256(dest) == MEDSAM2_SHA256:
        print(f"ok   {dest.relative_to(REPO)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"get  {MEDSAM2_URL}")
    with httpx.stream("GET", MEDSAM2_URL, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    if sha256(tmp) != MEDSAM2_SHA256:
        tmp.unlink()
        sys.exit("MedSAM2 checkpoint hash mismatch: refusing to use it")
    tmp.replace(dest)
    print(f"ok   {dest.relative_to(REPO)}")


def download_totalseg() -> None:
    home = CKPT / "totalseg"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["TOTALSEG_HOME_DIR"] = str(home)
    from totalsegmentator.config import set_config_key, setup_totalseg
    from totalsegmentator.libs import download_pretrained_weights

    setup_totalseg()
    set_config_key("send_usage_stats", False)  # no telemetry from this demo
    for task_id in TOTALSEG_TASK_IDS:
        download_pretrained_weights(task_id)
    print(f"ok   {home.relative_to(REPO)} (usage statistics disabled)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--skip-totalseg", action="store_true")
    args = ap.parse_args()
    download_medsam2()
    if not args.skip_totalseg:
        download_totalseg()


if __name__ == "__main__":
    main()
