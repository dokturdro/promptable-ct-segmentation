"""FastAPI app. Run: uv run uvicorn app.main:app --port 8000"""

from __future__ import annotations

import json
import os
from functools import cache
from typing import Annotated, Literal

os.environ.setdefault("TQDM_DISABLE", "1")  # MedSAM2 propagation progress bars

import numpy as np  # noqa: E402
import SimpleITK as sitk  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.config import settings, torch_device  # noqa: E402
from app.engines.base import BoxPrompt, PointPrompt  # noqa: E402
from app.engines.medsam2 import MedSAM2Engine  # noqa: E402
from app.engines.totalseg import TotalSegmentatorEngine  # noqa: E402
from app.export import ExportSegment, build_seg, read_seg_onto  # noqa: E402
from app.jobs import Job, jobs  # noqa: E402
from app.labels import segment_info, totalseg_catalogue  # noqa: E402
from app.pacs import list_segs, load_series, retrieve_instance, stow  # noqa: E402
from app.volume import Geometry, label_volumes_ml  # noqa: E402

app = FastAPI(title="Promptable CT segmentation demo (not for clinical use)")
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


@cache
def auto_engine() -> TotalSegmentatorEngine:
    return TotalSegmentatorEngine()


@cache
def prompt_engine() -> MedSAM2Engine:
    return MedSAM2Engine()


class SeriesRef(BaseModel):
    studyUID: str
    seriesUID: str


class WorldPoint(BaseModel):
    """Patient coordinates in mm (DICOM LPS)."""

    x: float
    y: float
    z: float
    label: Literal[0, 1] = 1


class WorldBox(BaseModel):
    """Two opposite corners on one axial slice, LPS mm."""

    p0: tuple[float, float, float]
    p1: tuple[float, float, float]


class PromptRequest(SeriesRef):
    points: list[WorldPoint] = Field(default_factory=list)
    box: WorldBox | None = None
    text: str | None = None


def _to_index(img: sitk.Image, xyz: tuple[float, float, float]) -> tuple[int, int, int]:
    idx = img.TransformPhysicalPointToIndex(tuple(float(v) for v in xyz))
    if not all(0 <= v < s for v, s in zip(idx, img.GetSize(), strict=True)):
        raise ValueError(f"point {xyz} is outside the volume")
    return idx


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "device": torch_device(), "orthanc": settings().orthanc_url}


@app.post("/series/geometry")
def series_geometry(req: SeriesRef) -> dict:
    """Backend voxel grid of a series (needed by the viewer to export manual-only edits)."""
    return Geometry.of(load_series(req.studyUID, req.seriesUID).image).to_json()


@app.get("/seg")
def seg_list(studyUID: str) -> list[dict]:
    return list_segs(studyUID)


class SegRef(SeriesRef):
    """``seriesUID`` is the CT the SEG should be shown on; the SEG is identified separately."""

    segSeriesUID: str
    segSOPInstanceUID: str


@app.post("/seg/load")
def seg_load(req: SegRef) -> dict:
    """Reads a DICOM-SEG back from the PACS onto the CT grid (round-trip verification)."""

    def run(job: Job) -> None:
        job.message = "Retrieving SEG and CT…"
        ct = load_series(req.studyUID, req.seriesUID)
        ds = retrieve_instance(req.studyUID, req.segSeriesUID, req.segSOPInstanceUID)
        refs = {r.SeriesInstanceUID for r in ds.get("ReferencedSeriesSequence", [])}
        if req.seriesUID not in refs:
            raise ValueError("this SEG does not reference the selected CT series")
        job.message = "Decoding SEG…"
        numbers, read = read_seg_onto(ds, ct.image)

        # Back to our label space: TotalSegmentator classes keep their label, others get 200+.
        by_name = {info.name: lbl for lbl, info in totalseg_catalogue().items()}
        lut = np.zeros(256, np.uint8)
        segments, next_user = [], 200
        for r in read:
            label = by_name.get(r.name)
            if label is None:
                label, next_user = next_user, next_user + 1
            lut[r.number] = label
            info = segment_info(label, r.name)
            segments.append(
                {
                    **info.to_json(),
                    "color": list(r.color or info.color),
                    "algorithm": r.algorithm,
                }
            )
        labels = lut[numbers]
        geom = Geometry.of(ct.image)
        vols = label_volumes_ml(labels, geom)
        for s in segments:
            s["volumeMl"] = round(vols.get(s["label"], 0.0), 1)
        job.array = labels
        job.result = {
            "geometry": geom.to_json(),
            "segments": segments,
            "sopInstanceUID": req.segSOPInstanceUID,
        }

    return {"jobId": jobs.submit("seg-load", run).id}


@app.post("/segment/auto")
def segment_auto(req: SeriesRef) -> dict:
    def run(job: Job) -> None:
        job.message = "Retrieving series from PACS…"
        series = load_series(req.studyUID, req.seriesUID)

        def progress(msg: str) -> None:
            job.message = msg

        res = auto_engine().segment(series.image, progress)
        arr = sitk.GetArrayFromImage(res.labels).astype(np.uint8)
        geom = Geometry.of(res.labels)
        vols = label_volumes_ml(arr, geom)
        job.array = arr
        job.result = {
            "seriesUID": req.seriesUID,
            "geometry": geom.to_json(),
            "segments": [
                {**info.to_json(), "volumeMl": round(vols.get(lbl, 0.0), 1)}
                for lbl, info in sorted(res.segments.items())
            ],
            "meta": res.meta,
        }

    return {"jobId": jobs.submit("auto", run).id}


@app.post("/segment/prompt")
def segment_prompt(req: PromptRequest) -> dict:
    if not req.points and req.box is None:
        raise HTTPException(422, "provide points and/or a box")

    def run(job: Job) -> None:
        job.message = "Retrieving series from PACS…"
        img = load_series(req.studyUID, req.seriesUID).image
        points = [PointPrompt(*_to_index(img, (p.x, p.y, p.z)), p.label) for p in req.points]
        boxes = []
        if req.box is not None:
            a, b = _to_index(img, req.box.p0), _to_index(img, req.box.p1)
            if a[2] != b[2]:
                raise ValueError("box corners must lie on the same axial slice")
            boxes.append(
                BoxPrompt(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]), a[2])
            )

        def progress(msg: str) -> None:
            job.message = msg

        res = prompt_engine().segment_prompt(img, points, progress, boxes=boxes, text=req.text)
        mask = sitk.GetArrayFromImage(res.mask).astype(np.uint8)
        nz = np.argwhere(mask)
        geom = Geometry.of(res.mask)
        if nz.size == 0:
            job.result = {"empty": True, "geometry": geom.to_json(), "meta": res.meta}
            return
        lo, hi = nz.min(axis=0), nz.max(axis=0) + 1
        job.array = np.ascontiguousarray(mask[lo[0] : hi[0], lo[1] : hi[1], lo[2] : hi[2]])
        job.result = {
            "empty": False,
            "geometry": geom.to_json(),
            # Crop of the full (k, j, i) grid: start index and shape, both in (k, j, i) order.
            "crop": {"start": lo.tolist(), "shape": (hi - lo).tolist()},
            "volumeMl": round(float(mask.sum()) * float(np.prod(geom.spacing)) / 1000, 1),
            "meta": res.meta,
        }

    return {"jobId": jobs.submit("prompt", run).id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job.to_json()


@app.get("/jobs/{job_id}/labelmap")
def get_job_labelmap(job_id: str) -> Response:
    """uint8 voxels, C-order (k, j, i): i fastest. Geometry (and crop) are in the job result."""
    job = jobs.get(job_id)
    if job is None or job.array is None:
        raise HTTPException(404, "no labelmap for this job")
    return Response(job.array.tobytes(), media_type="application/octet-stream")


class ExportSegmentIn(BaseModel):
    label: int = Field(ge=1, le=255)
    name: str
    color: tuple[int, int, int] | None = None
    algorithm: Literal["AUTOMATIC", "SEMIAUTOMATIC", "MANUAL"] = "MANUAL"
    algorithmName: str | None = None
    algorithmVersion: str | None = None


class ExportMeta(SeriesRef):
    segments: list[ExportSegmentIn]


@app.post("/export/dicom-seg")
def export_dicom_seg(meta: Annotated[str, Form()], labelmap: Annotated[UploadFile, File()]) -> dict:
    """Multipart: ``meta`` (JSON ExportMeta) + ``labelmap`` (uint8 (k, j, i) on the CT grid)."""
    m = ExportMeta.model_validate(json.loads(meta))
    series = load_series(m.studyUID, m.seriesUID)
    size_i, size_j, size_k = series.image.GetSize()
    raw = labelmap.file.read()
    if len(raw) != size_i * size_j * size_k:
        raise HTTPException(
            422, f"labelmap has {len(raw)} bytes, expected {size_i * size_j * size_k}"
        )
    labels = np.frombuffer(raw, dtype=np.uint8).reshape(size_k, size_j, size_i)

    catalogue = {info.name: info for info in totalseg_catalogue().values()}
    segments = []
    for s in m.segments:
        base = catalogue.get(s.name) or segment_info(s.label, s.name)
        info = type(base)(
            label=s.label,
            name=s.name,
            color=tuple(s.color) if s.color else base.color,  # type: ignore[arg-type]
            category=base.category,
            type=base.type,
            type_modifier=base.type_modifier,
        )
        segments.append(ExportSegment(info, s.algorithm, s.algorithmName, s.algorithmVersion))

    try:
        seg = build_seg(series.source_datasets(), labels, segments)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    stow([seg])
    return {
        "studyUID": seg.StudyInstanceUID,
        "seriesUID": seg.SeriesInstanceUID,
        "sopInstanceUID": seg.SOPInstanceUID,
        "segments": [d.SegmentLabel for d in seg.SegmentSequence],
        "frames": int(seg.NumberOfFrames),
    }
