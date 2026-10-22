"""Label map -> DICOM-SEG (highdicom), referencing the source CT series."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import highdicom as hd
import numpy as np
import pydicom
import SimpleITK as sitk
from pydicom.sr.codedict import codes

from app.labels import Code, SegmentInfo
from app.volume import image_from_affine, resample_labels_to

SOFTWARE = "promptable-ct-seg-demo 0.1"


@dataclass(frozen=True)
class ExportSegment:
    info: SegmentInfo
    algorithm: Literal["AUTOMATIC", "SEMIAUTOMATIC", "MANUAL"]
    algorithm_name: str | None = None  # e.g. "TotalSegmentator"
    algorithm_version: str | None = None


def _concept(c: Code) -> hd.sr.CodedConcept:
    return hd.sr.CodedConcept(value=c.value, scheme_designator=c.scheme, meaning=c.meaning)


def _description(number: int, seg: ExportSegment) -> hd.seg.SegmentDescription:
    algo_id = None
    if seg.algorithm != "MANUAL":
        algo_id = hd.AlgorithmIdentificationSequence(
            name=seg.algorithm_name or "unknown",
            family=codes.cid7162.ArtificialIntelligence,
            version=seg.algorithm_version or "unknown",
        )
    desc = hd.seg.SegmentDescription(
        segment_number=number,
        segment_label=seg.info.name,
        segmented_property_category=_concept(seg.info.category),
        segmented_property_type=_concept(seg.info.type),
        algorithm_type=seg.algorithm,
        algorithm_identification=algo_id,
        tracking_uid=hd.UID(),
        tracking_id=seg.info.name,
        display_color=hd.color.CIELabColor.from_rgb(*seg.info.color),
    )
    if seg.info.type_modifier is not None:
        # Laterality etc. lives inside the property type code item (e.g. "Kidney" + "Left").
        desc.SegmentedPropertyTypeCodeSequence[0].SegmentedPropertyTypeModifierCodeSequence = [
            _concept(seg.info.type_modifier)
        ]
    return desc


def _ensure_slice_thickness(seg_ds: pydicom.Dataset, source_images: list[pydicom.Dataset]) -> None:
    """Fills SliceThickness in the SEG's shared Pixel Measures when the source lacks it.

    Some public CTs (e.g. Pancreas-CT) carry an *empty* SliceThickness. highdicom 0.28 copies
    the source's pixel measures (even over explicitly passed ones when PixelSpacing matches),
    writes the item without SliceThickness, and its own reader then rejects the SEG. We use the
    measured slice spacing instead: an assumption of contiguous, non-overlapping slices.
    """
    item = seg_ds.SharedFunctionalGroupsSequence[0].PixelMeasuresSequence[0]
    if item.get("SliceThickness") not in (None, ""):
        return
    if "SpacingBetweenSlices" in item:
        thickness = float(item.SpacingBetweenSlices)
    elif len(source_images) > 1:
        iop = np.asarray(source_images[0].ImageOrientationPatient, dtype=float)
        delta = np.subtract(
            source_images[1].ImagePositionPatient, source_images[0].ImagePositionPatient
        ).astype(float)
        thickness = abs(float(np.dot(delta, np.cross(iop[:3], iop[3:]))))
    else:
        raise ValueError("cannot determine slice thickness for the SEG")
    item.SliceThickness = pydicom.valuerep.DSfloat(thickness, auto_format=True)


def build_seg(
    source_images: list[pydicom.Dataset],
    labels: np.ndarray,
    segments: list[ExportSegment],
    series_description: str = "AI segmentation (demo, not for clinical use)",
) -> pydicom.Dataset:
    """``labels`` is (k, j, i) on the source grid, k in the same order as ``source_images``.

    Label values are those in ``segments[*].info.label``; they are renumbered 1..N as DICOM
    requires. Segments with no voxels are dropped.
    """
    rows, cols = int(source_images[0].Rows), int(source_images[0].Columns)
    if labels.shape != (len(source_images), rows, cols):
        raise ValueError(
            f"labels shape {labels.shape} != source grid {(len(source_images), rows, cols)}"
        )
    present = [s for s in segments if np.any(labels == s.info.label)]
    if not present:
        raise ValueError("nothing to export: all segments are empty")
    if len(present) > 255:
        raise ValueError("at most 255 segments supported")
    remapped = np.zeros(labels.shape, dtype=np.uint8)
    for number, seg in enumerate(present, start=1):
        remapped[labels == seg.info.label] = number

    seg_ds = hd.seg.Segmentation(
        source_images=source_images,
        pixel_array=remapped,
        segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
        segment_descriptions=[_description(n, s) for n, s in enumerate(present, start=1)],
        series_instance_uid=hd.UID(),
        series_number=900,
        sop_instance_uid=hd.UID(),
        instance_number=1,
        manufacturer="Demo",
        manufacturer_model_name="promptable-ct-seg",
        software_versions=SOFTWARE,
        device_serial_number="0",
        content_label="SEGMENTATION",
        content_description="Not for clinical use",
        series_description=series_description,
    )
    _ensure_slice_thickness(seg_ds, source_images)
    return seg_ds


@dataclass(frozen=True)
class ReadSegment:
    number: int  # DICOM segment number in the SEG
    name: str
    algorithm: str
    color: tuple[int, int, int] | None


def read_seg_onto(
    seg_ds: pydicom.Dataset, reference: sitk.Image
) -> tuple[np.ndarray, list[ReadSegment]]:
    """Decodes a (non-overlapping) SEG and maps it onto ``reference``'s grid.

    Returns a (k, j, i) uint8 array whose values are the SEG's segment numbers.
    """
    seg = hd.seg.Segmentation.from_dataset(seg_ds)
    vol = seg.get_volume(combine_segments=True, relabel=False)
    img = image_from_affine(vol.array.astype(np.uint8), vol.affine)
    labels = sitk.GetArrayFromImage(resample_labels_to(img, sitk.Cast(reference, sitk.sitkUInt8)))
    segments = []
    for d in seg.SegmentSequence:
        color = None
        if "RecommendedDisplayCIELabValue" in d:
            lab = hd.color.CIELabColor.from_dicom_value(d.RecommendedDisplayCIELabValue)
            color = tuple(int(c) for c in lab.to_rgb())
        segments.append(
            ReadSegment(
                int(d.SegmentNumber), str(d.SegmentLabel), str(d.SegmentAlgorithmType), color
            )
        )
    return labels.astype(np.uint8), segments
