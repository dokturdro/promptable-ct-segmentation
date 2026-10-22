"""Download the demo CT (+ expert reference SEG) from NCI IDC and upload it to Orthanc.

Data: TCIA Pancreas-CT (CC BY 3.0) as mirrored by NCI Imaging Data Commons; the reference
pancreas segmentations in IDC are CC BY 4.0. Public, de-identified. See README for citations.

Usage (from repo root):
    uv run --project backend python scripts/seed_data.py               # download + upload
    uv run --project backend python scripts/seed_data.py --download-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
RAW_DIR = REPO / "data" / "raw"
DEFAULT_PATIENTS = ["PANCREAS_0001"]


def download(patients: list[str]) -> list[Path]:
    from idc_index import IDCClient

    client = IDCClient()
    idx = client.index
    sel = idx[(idx.collection_id == "pancreas_ct") & (idx.PatientID.isin(patients))]
    if sel.empty:
        sys.exit(f"no IDC series found for {patients}")
    for row in sel.itertuples():
        print(
            f"  {row.PatientID} {row.Modality:4} {row.instanceCount:4} files "
            f"{row.series_size_MB:7.1f} MB  {row.license_short_name}  {row.SeriesInstanceUID}"
        )
    print(f"Total: {sel.series_size_MB.sum():.1f} MB (IDC {client.get_idc_version()})")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client.download_from_selection(
        seriesInstanceUID=list(sel.SeriesInstanceUID),
        downloadDir=str(RAW_DIR),
        dirTemplate="%PatientID/%Modality_%SeriesInstanceUID",
    )
    return [RAW_DIR / p for p in patients]


def upload(dirs: list[Path], orthanc: str) -> None:
    files = sorted(f for d in dirs for f in d.rglob("*.dcm"))
    if not files:
        sys.exit(f"no .dcm files under {dirs}")
    with httpx.Client(base_url=orthanc, timeout=60) as http:
        http.get("/system").raise_for_status()
        for n, f in enumerate(files, 1):
            r = http.post(
                "/instances", content=f.read_bytes(), headers={"Content-Type": "application/dicom"}
            )
            r.raise_for_status()
            if n % 50 == 0 or n == len(files):
                print(f"  uploaded {n}/{len(files)}")
        stats = http.get("/statistics").json()
    print(f"Orthanc now holds {stats['CountStudies']} studies, {stats['CountInstances']} instances")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--patients", nargs="+", default=DEFAULT_PATIENTS)
    ap.add_argument("--orthanc", default="http://127.0.0.1:8042")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--upload-only", action="store_true")
    args = ap.parse_args()

    dirs = [RAW_DIR / p for p in args.patients]
    if not args.upload_only:
        dirs = download(args.patients)
    if not args.download_only:
        upload(dirs, args.orthanc)


if __name__ == "__main__":
    main()
