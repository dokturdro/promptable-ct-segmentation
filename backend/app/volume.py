"""Volume IO and orientation handling.

Canonical in-memory representation: a SimpleITK image in DICOM patient coordinates (LPS),
built from a DICOM series sorted along the slice normal. Array access via
``sitk.GetArrayViewFromImage`` gives (k, j, i) = (slice, row, column) order, which flattened
in C order is i-fastest, i.e. the same voxel order Cornerstone3D uses for volume scalar data.

NIfTI (RAS, i/j/k axis order) only appears at engine boundaries (TotalSegmentator), and
conversions go through the explicit affine helpers below rather than relying on any
library's implicit reorientation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np
import SimpleITK as sitk

LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])


@dataclass(frozen=True)
class Geometry:
    """Voxel grid in LPS. ``direction`` is row-major 3x3 with columns = i, j, k axes."""

    size: tuple[int, int, int]  # (i, j, k)
    spacing: tuple[float, float, float]
    origin: tuple[float, float, float]
    direction: tuple[float, ...]

    @classmethod
    def of(cls, img: sitk.Image) -> Geometry:
        return cls(
            size=tuple(int(s) for s in img.GetSize()),  # type: ignore[arg-type]
            spacing=tuple(float(s) for s in img.GetSpacing()),  # type: ignore[arg-type]
            origin=tuple(float(o) for o in img.GetOrigin()),  # type: ignore[arg-type]
            direction=tuple(float(d) for d in img.GetDirection()),
        )

    def affine_lps(self) -> np.ndarray:
        """4x4 matrix mapping (i, j, k, 1) to LPS millimetres."""
        a = np.eye(4)
        a[:3, :3] = np.asarray(self.direction).reshape(3, 3) * np.asarray(self.spacing)
        a[:3, 3] = self.origin
        return a

    def matches(self, other: Geometry, tol: float = 1e-3) -> bool:
        return (
            self.size == other.size
            and np.allclose(self.spacing, other.spacing, atol=tol)
            and np.allclose(self.origin, other.origin, atol=tol)
            and np.allclose(self.direction, other.direction, atol=tol)
        )

    def to_json(self) -> dict:
        return {
            "size": list(self.size),
            "spacing": list(self.spacing),
            "origin": list(self.origin),
            "direction": list(self.direction),
        }


def read_dicom_series(directory: Path) -> tuple[sitk.Image, list[str]]:
    """Read a single-series directory; returns the image and the files in slice order."""
    reader = sitk.ImageSeriesReader()
    series_ids = reader.GetGDCMSeriesIDs(str(directory))
    if len(series_ids) != 1:
        raise ValueError(f"expected exactly one series in {directory}, found {len(series_ids)}")
    # GDCM sorts by ImagePositionPatient projected on the slice normal.
    files = list(reader.GetGDCMSeriesFileNames(str(directory), series_ids[0]))
    reader.SetFileNames(files)
    img = reader.Execute()
    return img, files


def voxel_volume_ml(geom: Geometry) -> float:
    return float(np.prod(geom.spacing)) / 1000.0


def label_volumes_ml(labels: np.ndarray, geom: Geometry) -> dict[int, float]:
    counts = np.bincount(labels.ravel().astype(np.int64))
    vml = voxel_volume_ml(geom)
    return {int(v): float(c) * vml for v, c in enumerate(counts) if v != 0 and c > 0}


# --- NIfTI boundary -------------------------------------------------------------------------


def sitk_to_nifti(img: sitk.Image) -> nib.Nifti1Image:
    """Exact conversion: same voxels, RAS affine. No resampling, no reorientation."""
    geom = Geometry.of(img)
    data = sitk.GetArrayFromImage(img).transpose(2, 1, 0)  # (k,j,i) -> (i,j,k)
    affine = LPS_TO_RAS @ geom.affine_lps()
    nii = nib.Nifti1Image(np.ascontiguousarray(data), affine)
    nii.set_qform(affine, code=1)
    nii.set_sform(affine, code=1)
    return nii


def nifti_to_sitk(nii: nib.Nifti1Image) -> sitk.Image:
    """Exact inverse of :func:`sitk_to_nifti` for any (non-sheared) NIfTI affine."""
    affine_lps = LPS_TO_RAS @ nii.affine
    m = affine_lps[:3, :3]
    spacing = np.linalg.norm(m, axis=0)
    direction = m / spacing
    data = np.asanyarray(nii.dataobj)
    img = sitk.GetImageFromArray(np.ascontiguousarray(data.transpose(2, 1, 0)))
    img.SetSpacing(spacing.tolist())
    img.SetOrigin(affine_lps[:3, 3].tolist())
    img.SetDirection(direction.ravel().tolist())
    return img


def image_from_affine(array: np.ndarray, affine_lps: np.ndarray) -> sitk.Image:
    """Array with axes (a0, a1, a2) and a 4x4 affine mapping (a0, a1, a2, 1) to LPS.

    This is highdicom's ``Volume`` convention. SimpleITK's index (i, j, k) is (a2, a1, a0),
    so affine columns are reversed.
    """
    m = np.asarray(affine_lps, dtype=float)[:3, :3][:, ::-1]
    spacing = np.linalg.norm(m, axis=0)
    img = sitk.GetImageFromArray(np.ascontiguousarray(array))
    img.SetSpacing(spacing.tolist())
    img.SetOrigin(np.asarray(affine_lps, dtype=float)[:3, 3].tolist())
    img.SetDirection((m / spacing).ravel().tolist())
    return img


def resample_labels_to(labels: sitk.Image, reference: sitk.Image) -> sitk.Image:
    """Map a label image onto ``reference``'s grid; a pure copy when geometries already match."""
    if Geometry.of(labels).matches(Geometry.of(reference)):
        out = sitk.Image(labels)
        out.CopyInformation(reference)
        return out
    return sitk.Resample(
        labels, reference, sitk.Transform(), sitk.sitkNearestNeighbor, 0, labels.GetPixelID()
    )
