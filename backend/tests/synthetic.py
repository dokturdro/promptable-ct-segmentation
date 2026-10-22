"""Synthetic CT series with known geometry, for orientation tests."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid


@dataclass
class SeriesSpec:
    rows: int = 6
    cols: int = 8
    n_slices: int = 5
    pixel_spacing: tuple[float, float] = (0.7, 0.9)  # (between rows, between columns)
    slice_spacing: float = 2.5
    row_dir: tuple[float, float, float] = (1.0, 0.0, 0.0)  # direction of increasing column
    col_dir: tuple[float, float, float] = (0.0, 1.0, 0.0)  # direction of increasing row
    first_position: tuple[float, float, float] = (-100.0, -50.0, 30.0)
    # Physical order of slices along the normal: +1 ascending, -1 descending.
    stack_sign: int = 1
    # Pancreas-CT ships SliceThickness present-but-empty; reproduce that header quirk.
    empty_slice_thickness: bool = False


def _normal(spec: SeriesSpec) -> np.ndarray:
    return np.cross(spec.row_dir, spec.col_dir)


def slice_position(spec: SeriesSpec, s: int) -> np.ndarray:
    return np.asarray(spec.first_position) + spec.stack_sign * s * spec.slice_spacing * _normal(
        spec
    )


def voxel_lps(spec: SeriesSpec, row: int, col: int, s: int) -> np.ndarray:
    """LPS position of pixel (row, col) in the slice generated as number ``s``."""
    return (
        slice_position(spec, s)
        + col * spec.pixel_spacing[1] * np.asarray(spec.row_dir)
        + row * spec.pixel_spacing[0] * np.asarray(spec.col_dir)
    )


def write_series(
    spec: SeriesSpec, out_dir: Path, marker: tuple[int, int, int], shuffle: bool = True
) -> list[Dataset]:
    """Writes the series; ``marker`` = (row, col, slice number) gets value 1000, rest 0."""
    out_dir.mkdir(parents=True, exist_ok=True)
    study_uid, series_uid, for_uid = generate_uid(), generate_uid(), generate_uid()
    order = list(range(spec.n_slices))
    if shuffle:
        random.Random(0).shuffle(order)
    datasets = []
    for s in order:
        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
        ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.SOPClassUID = CTImageStorage
        ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID, ds.SeriesInstanceUID = study_uid, series_uid
        ds.FrameOfReferenceUID = for_uid
        ds.Modality = "CT"
        ds.PatientID, ds.PatientName = "SYNTH", "Synthetic^Phantom"
        ds.PatientBirthDate, ds.PatientSex = "", "O"
        ds.StudyDate, ds.StudyTime, ds.AccessionNumber = "20260101", "120000", ""
        ds.ReferringPhysicianName, ds.StudyID = "", "1"
        ds.SeriesNumber, ds.InstanceNumber = 1, s + 1
        ds.Manufacturer = "synthetic"
        ds.ImagePositionPatient = [float(v) for v in slice_position(spec, s)]
        ds.ImageOrientationPatient = [*map(float, spec.row_dir), *map(float, spec.col_dir)]
        ds.PixelSpacing = list(spec.pixel_spacing)
        ds.SliceThickness = None if spec.empty_slice_thickness else spec.slice_spacing
        ds.Rows, ds.Columns = spec.rows, spec.cols
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
        ds.RescaleIntercept, ds.RescaleSlope = 0, 1
        px = np.zeros((spec.rows, spec.cols), dtype=np.int16)
        if s == marker[2]:
            px[marker[0], marker[1]] = 1000
        ds.PixelData = px.tobytes()
        ds.save_as(out_dir / f"{ds.SOPInstanceUID}.dcm", enforce_file_format=True)
        datasets.append(ds)
    return datasets
