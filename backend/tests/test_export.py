"""DICOM-SEG export round-trip: voxels, references and codes must survive."""

from __future__ import annotations

import math
from pathlib import Path

import highdicom as hd
import numpy as np
import pydicom
import pytest
import SimpleITK as sitk

from app.export import ExportSegment, build_seg, read_seg_onto
from app.labels import segment_info, totalseg_catalogue
from app.volume import read_dicom_series
from tests.synthetic import SeriesSpec, write_series

c, s = math.cos(math.radians(20)), math.sin(math.radians(20))
SPECS = {
    "axial_descending": SeriesSpec(stack_sign=-1),
    "oblique": SeriesSpec(row_dir=(c, s, 0), col_dir=(0, 0, -1)),
    # Same as the Pancreas-CT headers: rows point anterior, origin 0, empty SliceThickness.
    "pancreas_ct_like": SeriesSpec(
        col_dir=(0, -1, 0), first_position=(0, 0, 0), empty_slice_thickness=True
    ),
}


@pytest.fixture(params=list(SPECS), ids=list(SPECS))
def ct(request, tmp_path: Path):
    write_series(SPECS[request.param], tmp_path / "ct", marker=(0, 0, 0))
    img, files = read_dicom_series(tmp_path / "ct")
    return img, [pydicom.dcmread(f) for f in files]


def _labels(img: sitk.Image) -> np.ndarray:
    k, j, i = sitk.GetArrayViewFromImage(img).shape
    lab = np.zeros((k, j, i), np.uint8)
    lab[1, 2, 3] = 5  # liver: single asymmetric voxel
    lab[3, 1:4, 5:7] = 7  # pancreas: block on another slice
    lab[4, 5, 0] = 3  # kidney_left (laterality modifier)
    return lab


def test_seg_roundtrip_preserves_voxels_and_references(ct, tmp_path: Path):
    img, sources = ct
    labels = _labels(img)
    cat = totalseg_catalogue()
    segs = [ExportSegment(cat[v], "AUTOMATIC", "TotalSegmentator", "2.18.0") for v in (3, 5, 7)]
    segs.append(ExportSegment(segment_info(200, "empty_structure"), "MANUAL"))  # dropped: empty

    path = tmp_path / "seg.dcm"
    build_seg(sources, labels, segs).save_as(path, enforce_file_format=True)
    seg = hd.seg.segread(path)

    # Linkage to the source series and every referenced instance exists in the source.
    assert seg.ReferencedSeriesSequence[0].SeriesInstanceUID == sources[0].SeriesInstanceUID
    assert seg.FrameOfReferenceUID == sources[0].FrameOfReferenceUID
    assert seg.StudyInstanceUID == sources[0].StudyInstanceUID
    src_uids = {d.SOPInstanceUID for d in sources}
    refs = seg.ReferencedSeriesSequence[0].ReferencedInstanceSequence
    assert {r.ReferencedSOPInstanceUID for r in refs} <= src_uids

    # Segments renumbered 1..N in the given order; codes + laterality kept.
    descs = list(seg.SegmentSequence)
    assert [d.SegmentLabel for d in descs] == ["kidney_left", "liver", "pancreas"]
    assert descs[1].SegmentedPropertyTypeCodeSequence[0].CodeValue == "10200004"  # Liver
    kidney_type = descs[0].SegmentedPropertyTypeCodeSequence[0]
    assert kidney_type.SegmentedPropertyTypeModifierCodeSequence[0].CodeMeaning == "Left"
    assert descs[1].SegmentAlgorithmType == "AUTOMATIC"

    # Voxel-exact after mapping the SEG's own geometry back onto the CT grid.
    back, read = read_seg_onto(pydicom.dcmread(path), img)
    expected = np.zeros_like(labels)
    for number, value in enumerate((3, 5, 7), start=1):
        expected[labels == value] = number
    np.testing.assert_array_equal(back, expected)
    assert [(r.number, r.name, r.algorithm) for r in read] == [
        (1, "kidney_left", "AUTOMATIC"),
        (2, "liver", "AUTOMATIC"),
        (3, "pancreas", "AUTOMATIC"),
    ]
    # Display colour survives the RGB -> CIELab -> RGB round trip (within rounding).
    assert np.allclose(read[1].color, cat[5].color, atol=2)


REAL = Path(__file__).resolve().parents[2] / "data" / "raw" / "PANCREAS_0001"


@pytest.mark.skipif(not REAL.exists(), reason="seed data not downloaded")
def test_reads_third_party_seg_onto_real_ct():
    """The expert SEG from IDC (written by another tool) decodes onto our CT grid."""
    img, _ = read_dicom_series(next(REAL.glob("CT_*")))
    labels, read = read_seg_onto(pydicom.dcmread(next(REAL.glob("SEG_*/*.dcm"))), img)
    assert labels.shape == sitk.GetArrayViewFromImage(img).shape
    assert [r.name for r in read] == ["Pancreas"]
    voxel_ml = float(np.prod(img.GetSpacing())) / 1000
    assert 40 < (labels == 1).sum() * voxel_ml < 80  # 58 mL reference pancreas


def test_shape_mismatch_rejected(ct):
    img, sources = ct
    with pytest.raises(ValueError, match="shape"):
        build_seg(sources[:-1], _labels(img), [ExportSegment(totalseg_catalogue()[5], "MANUAL")])
