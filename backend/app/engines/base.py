"""Engine interface. Engines take and return SimpleITK images on the source CT grid (LPS)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import SimpleITK as sitk

from app.labels import SegmentInfo

Progress = Callable[[str], None]


@dataclass(frozen=True)
class PointPrompt:
    """A click in voxel index space of the source CT: i = column, j = row, k = slice."""

    i: int
    j: int
    k: int
    label: Literal[0, 1]  # 1 = include (positive), 0 = exclude (negative)


@dataclass(frozen=True)
class BoxPrompt:
    """An in-plane box on slice k, voxel index space (inclusive corners)."""

    i0: int
    j0: int
    i1: int
    j1: int
    k: int


@dataclass
class AutoResult:
    labels: sitk.Image  # uint8/uint16 multi-label map on the CT grid
    segments: dict[int, SegmentInfo]
    meta: dict = field(default_factory=dict)


@dataclass
class PromptResult:
    mask: sitk.Image  # uint8 binary mask on the CT grid
    meta: dict = field(default_factory=dict)


@runtime_checkable
class AutoSegmentationEngine(Protocol):
    name: str

    def segment(self, image: sitk.Image, progress: Progress) -> AutoResult: ...


@runtime_checkable
class PromptSegmentationEngine(Protocol):
    name: str

    def segment_prompt(
        self,
        image: sitk.Image,
        points: list[PointPrompt],
        progress: Progress,
        boxes: list[BoxPrompt] | None = None,
        text: str | None = None,
    ) -> PromptResult: ...
