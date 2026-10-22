"""Orthanc access over DICOMweb (WADO-RS retrieve, STOW-RS store)."""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pydicom
import SimpleITK as sitk
from dicomweb_client.api import DICOMwebClient

from app.config import settings
from app.volume import read_dicom_series


@cache
def client() -> DICOMwebClient:
    return DICOMwebClient(url=settings().dicomweb_url)


@dataclass
class LoadedSeries:
    study_uid: str
    series_uid: str
    image: sitk.Image
    files: list[Path]  # in slice order (k)

    def source_datasets(self) -> list[pydicom.Dataset]:
        return [pydicom.dcmread(f) for f in self.files]


_lock = threading.Lock()
_loaded: dict[str, LoadedSeries] = {}


def _download(study_uid: str, series_uid: str, target: Path) -> None:
    tmp = target.with_suffix(".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for ds in client().retrieve_series(study_uid, series_uid):
        ds.save_as(tmp / f"{ds.SOPInstanceUID}.dcm", enforce_file_format=True)
    tmp.rename(target)


def load_series(study_uid: str, series_uid: str) -> LoadedSeries:
    """Retrieves (once) and reads a CT series; kept in memory for subsequent requests."""
    with _lock:
        if series_uid in _loaded:
            return _loaded[series_uid]
        target = settings().series_cache / series_uid
        if not target.exists():
            _download(study_uid, series_uid, target)
        image, files = read_dicom_series(target)
        loaded = LoadedSeries(study_uid, series_uid, image, [Path(f) for f in files])
        _loaded.clear()  # one volume at a time: keeps memory bounded for the demo
        _loaded[series_uid] = loaded
        return loaded


def stow(datasets: list[pydicom.Dataset]) -> None:
    client().store_instances(datasets)


SEG_STORAGE = "1.2.840.10008.5.1.4.1.1.66.4"


def list_segs(study_uid: str) -> list[dict]:
    rows = client().search_for_instances(
        study_instance_uid=study_uid,
        search_filters={"SOPClassUID": SEG_STORAGE},
        fields=["SeriesDescription", "ContentDate", "ContentTime", "SeriesNumber"],
    )
    out = []
    for r in rows:
        ds = pydicom.Dataset.from_json(r)
        out.append(
            {
                "seriesUID": ds.SeriesInstanceUID,
                "sopInstanceUID": ds.SOPInstanceUID,
                "description": str(ds.get("SeriesDescription", "")),
                "created": f"{ds.get('ContentDate', '')}{ds.get('ContentTime', '')}",
            }
        )
    return sorted(out, key=lambda r: r["created"], reverse=True)


def retrieve_instance(study_uid: str, series_uid: str, sop_uid: str) -> pydicom.Dataset:
    return client().retrieve_instance(study_uid, series_uid, sop_uid)
