"""Orientation round-trips: a single marker voxel must stay at the same LPS position."""

from __future__ import annotations

import math
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
import SimpleITK as sitk

from app.volume import (
    Geometry,
    label_volumes_ml,
    nifti_to_sitk,
    read_dicom_series,
    resample_labels_to,
    sitk_to_nifti,
)
from tests.synthetic import SeriesSpec, voxel_lps, write_series

c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
SPECS = {
    "axial_ascending": SeriesSpec(),
    "axial_descending": SeriesSpec(stack_sign=-1),
    # Coronal: rows run left->right, columns run head->feet (-S).
    "coronal": SeriesSpec(row_dir=(1, 0, 0), col_dir=(0, 0, -1)),
    "oblique": SeriesSpec(row_dir=(c, s, 0), col_dir=(0, 0, -1)),
    "flipped_rows": SeriesSpec(row_dir=(-1, 0, 0), col_dir=(0, -1, 0)),
}
MARKER = (4, 1, 3)  # (row, col, generated slice number); deliberately asymmetric


def _marker_index(arr_kji: np.ndarray) -> tuple[int, int, int]:
    k, j, i = np.argwhere(arr_kji > 0)[0]
    return int(i), int(j), int(k)


@pytest.fixture(params=list(SPECS), ids=list(SPECS))
def series(request, tmp_path: Path):
    spec = SPECS[request.param]
    write_series(spec, tmp_path / "dcm", MARKER)
    img, files = read_dicom_series(tmp_path / "dcm")
    return spec, img, files, tmp_path


def test_dicom_read_places_marker_at_expected_lps(series):
    spec, img, _, _ = series
    arr = sitk.GetArrayFromImage(img)
    assert int((arr > 0).sum()) == 1
    phys = img.TransformIndexToPhysicalPoint(_marker_index(arr))
    np.testing.assert_allclose(phys, voxel_lps(spec, *MARKER), atol=1e-4)


def test_slices_sorted_with_positive_spacing(series):
    spec, img, _, _ = series
    assert img.GetSpacing()[2] == pytest.approx(spec.slice_spacing)
    assert img.GetSpacing()[0] == pytest.approx(spec.pixel_spacing[1])  # i = column
    assert img.GetSpacing()[1] == pytest.approx(spec.pixel_spacing[0])  # j = row


def test_nifti_roundtrip_is_exact(series):
    spec, img, _, tmp = series
    path = tmp / "vol.nii.gz"
    nib.save(sitk_to_nifti(img), path)
    nii = nib.load(path)
    # RAS position of the marker = LPS with x, y negated.
    i, j, k = np.argwhere(np.asanyarray(nii.dataobj) > 0)[0]
    ras = nii.affine @ [i, j, k, 1]
    lps = voxel_lps(spec, *MARKER)
    np.testing.assert_allclose(ras[:3], [-lps[0], -lps[1], lps[2]], atol=1e-4)

    back = nifti_to_sitk(nii)
    assert Geometry.of(back).matches(Geometry.of(img))
    np.testing.assert_array_equal(sitk.GetArrayFromImage(back), sitk.GetArrayFromImage(img))


def test_engine_that_reorients_to_canonical_ras_maps_back(series):
    """TotalSegmentator-like engines may hand back a reoriented NIfTI; we must undo it."""
    spec, img, _, _ = series
    canonical = nib.as_closest_canonical(sitk_to_nifti(img))
    labels = nib.Nifti1Image(
        (np.asanyarray(canonical.dataobj) > 0).astype(np.uint8), canonical.affine
    )
    mapped = resample_labels_to(nifti_to_sitk(labels), img)
    arr = sitk.GetArrayFromImage(mapped)
    assert int(arr.sum()) == 1
    phys = img.TransformIndexToPhysicalPoint(_marker_index(arr))
    np.testing.assert_allclose(phys, voxel_lps(spec, *MARKER), atol=1e-4)


def test_label_volumes_ml():
    g = Geometry(
        size=(10, 10, 10),
        spacing=(1.0, 1.0, 2.0),
        origin=(0, 0, 0),
        direction=(1, 0, 0, 0, 1, 0, 0, 0, 1),
    )
    labels = np.zeros((10, 10, 10), np.uint8)
    labels[:5] = 3  # 500 voxels * 2 mm^3
    assert label_volumes_ml(labels, g) == {3: pytest.approx(1.0)}
