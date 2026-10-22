"""Segment catalogue: names, colours and SNOMED-CT codes for TotalSegmentator classes."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True)
class Code:
    scheme: str
    value: str
    meaning: str

    def to_json(self) -> dict:
        return {"scheme": self.scheme, "value": self.value, "meaning": self.meaning}


@dataclass(frozen=True)
class SegmentInfo:
    label: int
    name: str
    color: tuple[int, int, int]
    category: Code
    type: Code
    type_modifier: Code | None = None

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "name": self.name,
            "color": list(self.color),
            "category": self.category.to_json(),
            "type": self.type.to_json(),
            "typeModifier": self.type_modifier.to_json() if self.type_modifier else None,
        }


ANATOMICAL_STRUCTURE = Code("SCT", "123037004", "Anatomical Structure")
# Fallback for structures without a curated code (e.g. a user-drawn region).
TISSUE = Code("SCT", "85756007", "Tissue")


def _code(row: dict, prefix: str) -> Code | None:
    value = row.get(f"{prefix}.CodeValue", "").strip()
    if not value:
        return None
    return Code(
        row[f"{prefix}.CodingSchemeDesignator"].strip(),
        value,
        row[f"{prefix}.CodeMeaning"].strip(),
    )


@cache
def _snomed_rows() -> dict[str, dict]:
    path = resources.files("totalsegmentator") / "resources" / "totalsegmentator_snomed_mapping.csv"
    with path.open(encoding="utf-8") as f:
        return {row["Structure"]: row for row in csv.DictReader(f)}


def _default_color(label: int) -> tuple[int, int, int]:
    # Golden-angle hue walk: distinct, deterministic colours for uncoded labels.
    import colorsys

    r, g, b = colorsys.hsv_to_rgb((label * 0.381966) % 1.0, 0.65, 0.95)
    return int(r * 255), int(g * 255), int(b * 255)


def segment_info(label: int, name: str) -> SegmentInfo:
    row = _snomed_rows().get(name)
    if row is None:
        return SegmentInfo(label, name, _default_color(label), ANATOMICAL_STRUCTURE, TISSUE)
    color = tuple(int(c) for c in row["DicomRGBColor"].split(","))
    return SegmentInfo(
        label=label,
        name=name,
        color=color,  # type: ignore[arg-type]
        category=_code(row, "SegmentedPropertyCategoryCodeSequence") or ANATOMICAL_STRUCTURE,
        type=_code(row, "SegmentedPropertyTypeCodeSequence") or TISSUE,
        type_modifier=_code(row, "SegmentedPropertyTypeModifierCodeSequence"),
    )


@cache
def totalseg_catalogue(task: str = "total") -> dict[int, SegmentInfo]:
    from totalsegmentator.map_to_binary import class_map

    return {int(k): segment_info(int(k), v) for k, v in class_map[task].items()}
