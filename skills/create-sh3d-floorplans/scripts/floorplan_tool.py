#!/usr/bin/env python3
"""Validate canonical floorplan JSON and compile it into Sweet Home 3D files.

The implementation deliberately uses the Python standard library. PNG preview
generation is optional and uses Pillow when available.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET


SCHEMA_VERSION = "1.1.0"
# Target the broadly compatible 7.0 home format. The exporter uses only XML
# features supported by this generation and newer releases.
SH3D_XML_VERSION = "7000"
ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
SH3D_COLOR_RE = re.compile(r"^[0-9A-F]{8}$")
EPSILON = 1e-6
ENDPOINT_TOLERANCE_CM = 0.1

TOP_KEYS = {
    "schema_version",
    "project",
    "coordinate_system",
    "provenance",
    "environment",
    "levels",
    "cameras",
    "walls",
    "spaces",
    "openings",
    "objects",
}
# Everything except "environment", which is optional and defaults to
# ENVIRONMENT_DEFAULTS.
TOP_REQUIRED = TOP_KEYS - {"environment"}
ENVIRONMENT_KEYS = {
    "light_color",
    "ceiling_light_color",
    "sky_color",
    "ground_color",
    "walls_alpha",
}
# Sweet Home 3D scales the whole 3D view by lightColor, so it is the single
# strongest control over how a render reads. #D0D0D0 is the application's own
# default; anything brighter washes mid-tone surfaces out to white, which looks
# like wrong colours rather than wrong exposure.
ENVIRONMENT_DEFAULTS = {
    "light_color": "#D0D0D0",
    "ceiling_light_color": "#D0D0D0",
    "sky_color": "#CCE4FC",
    "ground_color": "#A8A8A8",
    "walls_alpha": 0.0,
}
PROJECT_KEYS = {
    "id",
    "name",
    "description",
    "selected_level_id",
    "default_wall_thickness_cm",
    "default_wall_height_cm",
}
COORDINATE_KEYS = {"units", "origin", "x_axis", "y_axis", "z_axis"}
PROVENANCE_KEYS = {
    "source_type",
    "measurement_status",
    "sources",
    "observations",
    "assumptions",
    "disclaimer",
}
SOURCE_KEYS = {"id", "kind", "uri", "description"}
PROVENANCE_ITEM_KEYS = {"id", "source_id", "statement", "confidence", "affects"}
EVIDENCE_KEYS = {"basis", "confidence", "note"}
LEVEL_KEYS = {
    "id",
    "name",
    "elevation_cm",
    "floor_thickness_cm",
    "ceiling_height_cm",
    "elevation_index",
    "visible",
    "viewable",
    "evidence",
}
CAMERA_KEYS = {
    "id",
    "name",
    "mode",
    "position",
    "target",
    "field_of_view_degrees",
    "lens",
    "active",
    "matches_source_id",
    "evidence",
}
CAMERA_POINT_KEYS = {"x_cm", "y_cm", "z_cm"}
WALL_KEYS = {
    "id",
    "level_id",
    "name",
    "role",
    "start",
    "end",
    "thickness_cm",
    "height_cm",
    "height_at_end_cm",
    "color",
    "shape_params",
    "evidence",
}
SPACE_KEYS = {
    "id",
    "level_id",
    "name",
    "kind",
    "polygon",
    "floor",
    "ceiling",
    "area_visible",
    "evidence",
}
SURFACE_KEYS = {"enabled", "color", "shininess"}
OPENING_KEYS = {
    "id",
    "wall_id",
    "name",
    "type",
    "center_offset_cm",
    "width_cm",
    "height_cm",
    "sill_height_cm",
    "depth_cm",
    "color",
    "swing",
    "shape_params",
    "evidence",
}
SWING_KEYS = {"hinge", "direction", "angle_degrees"}
OBJECT_KEYS = {
    "id",
    "facing",
    "level_id",
    "name",
    "category",
    "shape",
    "center",
    "width_cm",
    "depth_cm",
    "height_cm",
    "elevation_cm",
    "angle_degrees",
    "color",
    "movable",
    "shape_params",
    "light_power",
    "light_color",
    "evidence",
}
POINT_KEYS = {"x_cm", "y_cm"}
BUILTIN_SHAPES = {
    "box",
    "cylinder",
    "bookcase",
    "books",
    "cabinet",
    "counter",
    "sofa",
    "armchair",
    "chair",
    "dining-table",
    "round-table",
    "island",
    "linear-pendant",
    "curved-banquette",
    "chandelier",
    "fireplace",
    "coffered-ceiling",
    "wall-panel",
    "sconce",
    "deco-railing",
    "rug",
    "curtain",
    "sunburst",
    "crown-moulding",
}

# Shapes that sit overhead and span most of their level. Filling them in the
# 2D plan would hide everything beneath, so they are drawn as outlines only.
OVERHEAD_SHAPES = {"coffered-ceiling", "crown-moulding"}
# Which way a piece points. The front is the model's +Z (depth) axis, and Sweet
# Home 3D maps it to plan south at angle 0:
#
#     front(angle) = (-sin angle, cos angle)
#     0 -> south, 90 -> west, 180 -> north, 270 -> east
#
# Established by rendering a bookcase against a wall at 0 and at 180: at 0 its
# shelves face the room, at 180 the room sees a flat back panel. A sofa test
# was tried first and proved unreadable -- from an elevated camera the tall
# back and the arms swap apparent order, so both orientations look plausible.
# Use a piece with an unmistakable front, and read it at a distance where the
# difference is a whole surface rather than a silhouette detail.
CARDINAL_ANGLES = {"south": 0.0, "west": 90.0, "north": 180.0, "east": 270.0}
FACING_TOLERANCE_DEGREES = 6.0
# Shapes whose geometry has a distinguishable front: a back panel, doors, or a
# seat. Everything else is symmetric enough that its angle does not read.
DIRECTIONAL_SHAPES = {
    "sofa",
    "armchair",
    "chair",
    "bookcase",
    "cabinet",
    "counter",
    "fireplace",
    "sconce",
    "wall-panel",
    "curved-banquette",
}
# The banquette is the exception: its opening is cut around the model's +X
# (width) axis rather than its depth axis, so it faces 90 degrees off the rest.
WIDTH_FACING_SHAPES = {"curved-banquette"}
# How far in front of a piece to test for a wall.
FRONT_PROBE_CM = 25.0

# Likewise underfoot: a filled rug would hide the furniture standing on it.
UNDERFOOT_SHAPES = {"rug"}
OUTLINE_ONLY_SHAPES = OVERHEAD_SHAPES | UNDERFOOT_SHAPES

# Shapes whose geometry is generated from JSON parameters. Each entry maps a
# parameter name to (minimum, maximum, default). Values are validated against
# this table and then baked into a per-variant OBJ file, so two objects that
# share a shape but not its parameters get distinct embedded models.
SHAPE_PARAMETERS: dict[str, dict[str, tuple[float, float, float]]] = {
    "coffered-ceiling": {
        "columns": (1, 12, 3),
        "rows": (1, 12, 3),
        "beam_ratio": (0.02, 0.4, 0.12),
        "cross_braces": (0, 1, 0),
    },
    "wall-panel": {
        "columns": (1, 8, 1),
        "rows": (1, 8, 1),
        "border_ratio": (0.02, 0.45, 0.12),
    },
    "deco-railing": {
        "bays": (1, 24, 4),
        "rays": (2, 16, 7),
    },
    "chandelier": {
        "tiers": (1, 6, 3),
        "drop_ratio": (0.0, 0.9, 0.35),
    },
    "bookcase": {
        "shelves": (1, 10, 4),
        "base_ratio": (0.02, 0.5, 0.08),
    },
    "rug": {
        "border_ratio": (0.02, 0.45, 0.08),
    },
    "curtain": {
        "folds": (2, 24, 6),
    },
    "sunburst": {
        "rays": (2, 24, 9),
    },
    "crown-moulding": {
        "steps": (1, 6, 3),
        "band_ratio": (0.005, 0.2, 0.03),
    },
    "window": {
        "columns": (1, 8, 2),
        "rows": (1, 16, 2),
    },
}


class ValidationResult:
    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def error(self, code: str, path: str, message: str) -> None:
        self.errors.append({"code": code, "path": path, "message": message})

    def warning(self, code: str, path: str, message: str) -> None:
        self.warnings.append({"code": code, "path": path, "message": message})

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_keys(
    result: ValidationResult,
    value: Any,
    path: str,
    allowed: set[str],
    required: set[str],
) -> bool:
    if not isinstance(value, dict):
        result.error("type.object", path, "Expected an object.")
        return False
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    for key in missing:
        result.error("property.missing", f"{path}/{key}", "Required property is missing.")
    for key in extra:
        result.error("property.unknown", f"{path}/{key}", "Unknown property is not allowed.")
    return not missing


def _validate_id(result: ValidationResult, value: Any, path: str) -> bool:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        result.error(
            "id.invalid",
            path,
            "ID must use lowercase letters, digits, and internal hyphens, and start with a letter.",
        )
        return False
    return True


def _validate_text(
    result: ValidationResult, value: Any, path: str, *, minimum: int = 1, maximum: int = 2000
) -> bool:
    if not isinstance(value, str) or not (minimum <= len(value) <= maximum):
        result.error("text.invalid", path, f"Expected a string of {minimum}–{maximum} characters.")
        return False
    return True


def _validate_number(
    result: ValidationResult,
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> bool:
    if not _is_number(value):
        result.error("number.invalid", path, "Expected a finite number.")
        return False
    number = float(value)
    if minimum is not None:
        bad = number <= minimum if exclusive_minimum else number < minimum
        if bad:
            comparator = "greater than" if exclusive_minimum else "at least"
            result.error("number.minimum", path, f"Expected a value {comparator} {minimum}.")
            return False
    if maximum is not None and number > maximum:
        result.error("number.maximum", path, f"Expected a value no greater than {maximum}.")
        return False
    return True


def _validate_bool(result: ValidationResult, value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        result.error("type.boolean", path, "Expected true or false.")
        return False
    return True


def _validate_enum(
    result: ValidationResult, value: Any, path: str, choices: Iterable[str]
) -> bool:
    allowed = set(choices)
    if value not in allowed:
        result.error("enum.invalid", path, f"Expected one of: {', '.join(sorted(allowed))}.")
        return False
    return True


def _validate_color(result: ValidationResult, value: Any, path: str) -> bool:
    if not isinstance(value, str) or not COLOR_RE.fullmatch(value):
        result.error("color.invalid", path, "Expected a color in #RRGGBB form.")
        return False
    return True


def _validate_point(result: ValidationResult, value: Any, path: str) -> bool:
    required = set(POINT_KEYS)
    if not _validate_keys(result, value, path, POINT_KEYS, required):
        return False
    return (
        _validate_number(result, value.get("x_cm"), f"{path}/x_cm", minimum=-100000, maximum=100000)
        and _validate_number(
            result, value.get("y_cm"), f"{path}/y_cm", minimum=-100000, maximum=100000
        )
    )


def _validate_camera_point(result: ValidationResult, value: Any, path: str) -> bool:
    if not _validate_keys(
        result, value, path, CAMERA_POINT_KEYS, CAMERA_POINT_KEYS
    ):
        return False
    return all(
        _validate_number(
            result,
            value.get(axis),
            f"{path}/{axis}",
            minimum=-100000,
            maximum=100000,
        )
        for axis in ("x_cm", "y_cm", "z_cm")
    )


def resolve_shape_params(shape: str, raw: Any) -> dict[str, float]:
    """Merge supplied shape parameters over the defaults for ``shape``.

    Unknown shapes resolve to an empty mapping so unparameterised models keep a
    stable, suffix-free file name.
    """
    spec = SHAPE_PARAMETERS.get(shape)
    if spec is None:
        return {}
    supplied = raw if isinstance(raw, dict) else {}
    resolved: dict[str, float] = {}
    for name, (minimum, maximum, default) in spec.items():
        value = supplied.get(name, default)
        if not _is_number(value):
            value = default
        resolved[name] = min(max(float(value), minimum), maximum)
    return resolved


# Shapes whose detailing has a real-world thickness -- glazing bars, panel
# rails -- rather than a thickness proportional to the piece. An OBJ is written
# in a unit cube and then scaled independently on each axis, so a ratio that
# looks right on a square window becomes a 14 cm muntin on a 124 x 590 one.
# Feeding the piece's proportions in lets the generator pre-compensate.
ASPECT_AWARE_SHAPES = {"window", "wall-panel"}


def _quantised_aspect(width: Any, height: Any) -> float:
    """Width-to-height ratio, rounded so near-identical pieces share one model."""
    if not (_is_number(width) and _is_number(height)) or float(height) <= 0:
        return 1.0
    ratio = float(width) / float(height)
    ratio = min(max(ratio, 0.05), 20.0)
    # Round in log space: the correction matters proportionally, so 0.2 and 0.3
    # deserve separate models while 8 and 9 do not. Quarter-octave steps.
    return round(2 ** (round(math.log2(ratio) * 4) / 4), 4)


def resolve_geometry_params(
    shape: str, raw: Any, width: Any = None, height: Any = None
) -> dict[str, float]:
    """Authored parameters plus any the geometry derives from the piece itself.

    ``aspect`` is computed, never authored, so it is injected after validation
    and is absent from SHAPE_PARAMETERS. It still takes part in the model name,
    which keeps two differently proportioned windows on separate meshes.
    """
    params = resolve_shape_params(shape, raw)
    if params and shape in ASPECT_AWARE_SHAPES:
        params["aspect"] = _quantised_aspect(width, height)
    return params


def _validate_shape_params(
    result: ValidationResult, shape: Any, value: Any, path: str
) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        result.error("type.object", path, "Expected an object.")
        return
    spec = SHAPE_PARAMETERS.get(shape) if isinstance(shape, str) else None
    if spec is None:
        result.error(
            "shape_params.unsupported",
            path,
            f"Shape {shape!r} does not accept parameters.",
        )
        return
    for name, parameter in value.items():
        if name not in spec:
            result.error(
                "shape_params.unknown",
                f"{path}/{name}",
                f"Unknown parameter for shape {shape!r}.",
            )
            continue
        minimum, maximum, _ = spec[name]
        _validate_number(
            result, parameter, f"{path}/{name}", minimum=minimum, maximum=maximum
        )


def _validate_evidence(result: ValidationResult, value: Any, path: str) -> None:
    if not _validate_keys(result, value, path, EVIDENCE_KEYS, EVIDENCE_KEYS):
        return
    basis_valid = _validate_enum(
        result,
        value.get("basis"),
        f"{path}/basis",
        {"observed", "estimated", "specified", "derived"},
    )
    confidence_valid = _validate_number(
        result, value.get("confidence"), f"{path}/confidence", minimum=0, maximum=1
    )
    _validate_text(result, value.get("note"), f"{path}/note", maximum=1000)
    if (
        basis_valid
        and confidence_valid
        and value.get("basis") == "estimated"
        and float(value.get("confidence", 0)) > 0.75
    ):
        result.warning(
            "evidence.estimated-high-confidence",
            path,
            "Estimated geometry has confidence above 0.75; confirm this is justified.",
        )


def _point_tuple(point: dict[str, Any]) -> tuple[float, float]:
    return float(point["x_cm"]), float(point["y_cm"])


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _signed_area(points: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )


def _orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    if abs(value) <= EPSILON:
        return 0
    return 1 if value > 0 else 2


def _on_segment(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> bool:
    return (
        min(a[0], c[0]) - EPSILON <= b[0] <= max(a[0], c[0]) + EPSILON
        and min(a[1], c[1]) - EPSILON <= b[1] <= max(a[1], c[1]) + EPSILON
    )


def _segments_intersect(
    p1: tuple[float, float],
    q1: tuple[float, float],
    p2: tuple[float, float],
    q2: tuple[float, float],
) -> bool:
    o1 = _orientation(p1, q1, p2)
    o2 = _orientation(p1, q1, q2)
    o3 = _orientation(p2, q2, p1)
    o4 = _orientation(p2, q2, q1)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _on_segment(p1, p2, q1))
        or (o2 == 0 and _on_segment(p1, q2, q1))
        or (o3 == 0 and _on_segment(p2, p1, q2))
        or (o4 == 0 and _on_segment(p2, q1, q2))
    )


def _polygon_is_simple(points: list[tuple[float, float]]) -> bool:
    count = len(points)
    for i in range(count):
        a1, a2 = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or (i + 1) % count == j:
                continue
            b1, b2 = points[j], points[(j + 1) % count]
            if _segments_intersect(a1, a2, b1, b2):
                return False
    return True


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = (yi > y) != (yj > y) and x < (
            (xj - xi) * (y - yi) / ((yj - yi) or EPSILON) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _wall_length(wall: dict[str, Any]) -> float:
    return _distance(_point_tuple(wall["start"]), _point_tuple(wall["end"]))


MIN_CAMERA_CLEARANCE_CM = 90.0
# Below this, a piece lies flat enough on the floor that it cannot obstruct an
# observer no matter how close it comes: rugs, floor inlays, thresholds.
FLAT_OBJECT_HEIGHT_CM = 12.0


def _floor_polygons(
    spaces_by_level: dict[str, list[dict[str, Any]]]
) -> list[list[tuple[float, float]]]:
    polygons: list[list[tuple[float, float]]] = []
    for spaces in spaces_by_level.values():
        for space in spaces:
            floor = space.get("floor")
            polygon = space.get("polygon")
            if (
                isinstance(floor, dict)
                and floor.get("enabled")
                and isinstance(polygon, list)
                and len(polygon) >= 3
            ):
                try:
                    polygons.append([_point_tuple(point) for point in polygon])
                except (KeyError, TypeError):
                    continue
    return polygons


def _distance_to_polygon(
    point: tuple[float, float], polygon: list[tuple[float, float]]
) -> float:
    """Shortest distance from a point to a polygon; zero if inside."""
    if _point_in_polygon(point, polygon):
        return 0.0
    best = math.inf
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        segment_x, segment_y = end[0] - start[0], end[1] - start[1]
        length_squared = segment_x * segment_x + segment_y * segment_y
        if length_squared <= EPSILON:
            best = min(best, _distance(point, start))
            continue
        t = ((point[0] - start[0]) * segment_x + (point[1] - start[1]) * segment_y) / length_squared
        t = min(max(t, 0.0), 1.0)
        best = min(
            best,
            _distance(point, (start[0] + t * segment_x, start[1] + t * segment_y)),
        )
    return best


def facing_vector(angle_degrees: float, shape: str = "") -> tuple[float, float]:
    """Unit vector the piece's front points along, in plan coordinates."""
    radians = math.radians(angle_degrees)
    if shape in WIDTH_FACING_SHAPES:
        return (math.cos(radians), math.sin(radians))
    return (-math.sin(radians), math.cos(radians))


def angle_for_facing(
    centre: tuple[float, float], facing: Any, shape: str = ""
) -> float | None:
    """Angle that makes a piece face a cardinal direction or a point."""
    if isinstance(facing, str):
        angle = CARDINAL_ANGLES.get(facing)
    elif isinstance(facing, dict) and _is_number(facing.get("x_cm")):
        dx = float(facing["x_cm"]) - centre[0]
        dy = float(facing["y_cm"]) - centre[1]
        if math.hypot(dx, dy) < EPSILON:
            return None
        angle = (
            math.degrees(math.atan2(dy, dx))
            if shape in WIDTH_FACING_SHAPES
            else math.degrees(math.atan2(-dx, dy))
        )
    else:
        return None
    if angle is None:
        return None
    return angle % 360.0


def _validate_facing(
    result: ValidationResult,
    document: dict[str, Any],
    spaces_by_level: dict[str, list[dict[str, Any]]],
) -> None:
    """Check declared facing against the angle, and catch seats facing walls.

    Orientation is the error that survives every other kind of review: a
    backwards sofa still validates, still renders, and still looks like
    furniture from above.
    """
    polygons_by_level = {
        level: _floor_polygons({level: spaces})
        for level, spaces in spaces_by_level.items()
    }
    for index, obj in enumerate(document.get("objects") or []):
        if not isinstance(obj, dict):
            continue
        path = f"/objects/{index}"
        shape = obj.get("shape")
        centre = obj.get("center")
        if not isinstance(centre, dict) or not _is_number(centre.get("x_cm")):
            continue
        point = _point_tuple(centre)
        angle = obj.get("angle_degrees")
        if not _is_number(angle):
            continue

        if "facing" in obj:
            facing = obj["facing"]
            valid_shape = isinstance(facing, str) and facing in CARDINAL_ANGLES
            valid_point = (
                isinstance(facing, dict)
                and set(facing) == POINT_KEYS
                and _is_number(facing.get("x_cm"))
                and _is_number(facing.get("y_cm"))
            )
            if not (valid_shape or valid_point):
                result.error(
                    "facing.invalid",
                    f"{path}/facing",
                    "Expected one of north, east, south, west, or a point "
                    "with x_cm and y_cm.",
                )
                continue
            expected = angle_for_facing(point, facing, shape or "")
            if expected is None:
                result.error(
                    "facing.invalid",
                    f"{path}/facing",
                    "A facing point must not coincide with the object centre.",
                )
                continue
            difference = abs((float(angle) - expected + 180.0) % 360.0 - 180.0)
            if difference > FACING_TOLERANCE_DEGREES:
                result.error(
                    "facing.mismatch",
                    f"{path}/angle_degrees",
                    f"angle_degrees {float(angle):g} does not face {facing!r}; "
                    f"use {expected:g}. Angle 0 faces south, 90 west, "
                    f"180 north, 270 east.",
                )

        if shape not in DIRECTIONAL_SHAPES:
            continue
        polygons = polygons_by_level.get(obj.get("level_id")) or []
        if not polygons:
            continue
        try:
            reach = float(obj["depth_cm"]) / 2 + FRONT_PROBE_CM
        except (KeyError, TypeError, ValueError):
            continue
        vector = facing_vector(float(angle), shape or "")
        front = (point[0] + vector[0] * reach, point[1] + vector[1] * reach)
        if any(_point_in_polygon(front, polygon) for polygon in polygons):
            continue
        suggestion = (float(angle) + 180.0) % 360.0
        result.warning(
            "facing.into-wall",
            f"{path}/angle_degrees",
            f"{obj.get('id')!r} has its front toward a wall or outside the "
            f"room. If it is meant to face into the room, use "
            f"{suggestion:g} degrees. Angle 0 faces south, 90 west, 180 "
            f"north, 270 east.",
        )


def _validate_camera_placement(
    result: ValidationResult,
    document: dict[str, Any],
    cameras: list[Any],
    spaces_by_level: dict[str, list[dict[str, Any]]],
) -> None:
    """Catch observer cameras standing outside the building or inside furniture.

    A camera placed beyond the walls renders the room cut open from outside,
    which looks like a modelling fault but is purely a camera error.
    """
    polygons = _floor_polygons(spaces_by_level)
    if not polygons:
        return
    objects = document.get("objects")
    objects = objects if isinstance(objects, list) else []
    for i, camera in enumerate(cameras):
        if not isinstance(camera, dict) or camera.get("mode") != "observer":
            continue
        position = camera.get("position")
        if not isinstance(position, dict) or not _is_number(position.get("x_cm")):
            continue
        try:
            eye = (float(position["x_cm"]), float(position["y_cm"]))
        except (KeyError, TypeError, ValueError):
            continue
        path = f"/cameras/{i}"
        if not any(_point_in_polygon(eye, polygon) for polygon in polygons):
            outside_message = (
                "Observer camera stands outside every floor-enabled space. "
                "Sweet Home 3D will render the room cut open from outside."
            )
            if camera.get("matches_source_id"):
                result.error("camera.outside-space", f"{path}/position", outside_message)
            else:
                result.warning("camera.outside-space", f"{path}/position", outside_message)
            continue
        eye_height = float(position["z_cm"]) if _is_number(position.get("z_cm")) else 0.0
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("shape") in OUTLINE_ONLY_SHAPES:
                continue
            centre = obj.get("center")
            if not isinstance(centre, dict) or not _is_number(centre.get("x_cm")):
                continue
            try:
                # Anything sitting entirely above the eye cannot crowd the frame.
                if float(obj["elevation_cm"]) >= eye_height:
                    continue
                # Nor can anything lying flat on the floor: a rug or an inlay
                # slab is wide enough to reach the camera but never blocks it.
                if float(obj["height_cm"]) <= FLAT_OBJECT_HEIGHT_CM:
                    continue
                # Measure to the footprint, not to the centre less half a
                # diagonal: that approximation treats a 4 m wall run as a blob
                # and reports a camera in the middle of the room as blocked.
                gap = _distance_to_polygon(
                    eye,
                    _rotated_rectangle(
                        _point_tuple(centre),
                        float(obj["width_cm"]),
                        float(obj["depth_cm"]),
                        float(obj["angle_degrees"]),
                    ),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if gap < MIN_CAMERA_CLEARANCE_CM:
                result.warning(
                    "camera.clearance",
                    f"{path}/position",
                    f"{obj.get('id')!r} sits about {max(gap, 0):.0f} cm from this camera "
                    f"and will crowd or block the frame; keep at least "
                    f"{MIN_CAMERA_CLEARANCE_CM:.0f} cm of clearance.",
                )


def _validate_enclosure(
    result: ValidationResult,
    document: dict[str, Any],
    spaces_by_level: dict[str, list[dict[str, Any]]],
) -> None:
    """Warn when a room has no ceiling surface above it.

    A double-height space whose ceiling is disabled renders open to the sky, so
    an interior camera shows background instead of a soffit.
    """
    objects = document.get("objects")
    objects = objects if isinstance(objects, list) else []
    overhead: list[list[tuple[float, float]]] = []
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("shape") not in OVERHEAD_SHAPES:
            continue
        centre = obj.get("center")
        if not isinstance(centre, dict) or not _is_number(centre.get("x_cm")):
            continue
        try:
            overhead.append(
                _rotated_rectangle(
                    _point_tuple(centre),
                    float(obj["width_cm"]),
                    float(obj["depth_cm"]),
                    float(obj["angle_degrees"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    for spaces in spaces_by_level.values():
        for space in spaces:
            if space.get("kind") != "room":
                continue
            ceiling = space.get("ceiling")
            polygon = space.get("polygon")
            if not isinstance(ceiling, dict) or ceiling.get("enabled"):
                continue
            if not isinstance(polygon, list) or len(polygon) < 3:
                continue
            try:
                centroid = _space_centroid([_point_tuple(p) for p in polygon])
            except (KeyError, TypeError):
                continue
            if any(_point_in_polygon(centroid, cover) for cover in overhead):
                continue
            result.warning(
                "space.unenclosed",
                f"/spaces/{space.get('id')}/ceiling/enabled",
                "Room has no ceiling surface and no overhead shape covering it; "
                "an interior camera will see sky above the walls. Enable the "
                "ceiling, or add a coffered-ceiling object at the top of the volume.",
            )


def validate_document(document: Any) -> ValidationResult:
    result = ValidationResult()
    if not _validate_keys(result, document, "", TOP_KEYS, TOP_REQUIRED):
        return result

    if document.get("schema_version") != SCHEMA_VERSION:
        result.error(
            "schema.version",
            "/schema_version",
            f"Expected schema_version {SCHEMA_VERSION!r}.",
        )

    project = document.get("project")
    project_required = {
        "id",
        "name",
        "selected_level_id",
        "default_wall_thickness_cm",
        "default_wall_height_cm",
    }
    if _validate_keys(result, project, "/project", PROJECT_KEYS, project_required):
        _validate_id(result, project.get("id"), "/project/id")
        _validate_text(result, project.get("name"), "/project/name", maximum=200)
        if "description" in project:
            _validate_text(
                result, project.get("description"), "/project/description", minimum=0, maximum=2000
            )
        _validate_id(result, project.get("selected_level_id"), "/project/selected_level_id")
        _validate_number(
            result,
            project.get("default_wall_thickness_cm"),
            "/project/default_wall_thickness_cm",
            minimum=0,
            maximum=100,
            exclusive_minimum=True,
        )
        _validate_number(
            result,
            project.get("default_wall_height_cm"),
            "/project/default_wall_height_cm",
            minimum=0,
            maximum=2000,
            exclusive_minimum=True,
        )

    if "environment" in document:
        environment = document["environment"]
        if _validate_keys(result, environment, "/environment", ENVIRONMENT_KEYS, set()):
            for key in ("light_color", "ceiling_light_color", "sky_color", "ground_color"):
                if key in environment:
                    _validate_color(result, environment[key], f"/environment/{key}")
            if "walls_alpha" in environment:
                _validate_number(
                    result,
                    environment["walls_alpha"],
                    "/environment/walls_alpha",
                    minimum=0,
                    maximum=1,
                )

    coordinate = document.get("coordinate_system")
    if _validate_keys(
        result, coordinate, "/coordinate_system", COORDINATE_KEYS, COORDINATE_KEYS
    ):
        expected = {
            "units": "cm",
            "origin": "plan-top-left",
            "x_axis": "right-east",
            "y_axis": "down-south",
            "z_axis": "up",
        }
        for key, expected_value in expected.items():
            if coordinate.get(key) != expected_value:
                result.error(
                    "coordinate.constant",
                    f"/coordinate_system/{key}",
                    f"Expected {expected_value!r}.",
                )

    geometry_ids: dict[str, str] = {}
    duplicate_ids: set[str] = set()
    source_ids: set[str] = set()

    provenance = document.get("provenance")
    if _validate_keys(
        result, provenance, "/provenance", PROVENANCE_KEYS, PROVENANCE_KEYS
    ):
        _validate_enum(
            result,
            provenance.get("source_type"),
            "/provenance/source_type",
            {"image", "text", "measured-drawing", "mixed"},
        )
        _validate_enum(
            result,
            provenance.get("measurement_status"),
            "/provenance/measurement_status",
            {"estimated", "measured", "mixed"},
        )
        _validate_text(
            result,
            provenance.get("disclaimer"),
            "/provenance/disclaimer",
            minimum=20,
            maximum=2000,
        )
        if (
            provenance.get("source_type") == "image"
            and provenance.get("measurement_status") == "measured"
        ):
            result.error(
                "provenance.image-measured",
                "/provenance/measurement_status",
                "An image-only project cannot claim all geometry is measured.",
            )
        sources = provenance.get("sources")
        if not isinstance(sources, list) or not sources:
            result.error("array.required", "/provenance/sources", "Expected at least one source.")
        else:
            for i, source in enumerate(sources):
                path = f"/provenance/sources/{i}"
                if not _validate_keys(result, source, path, SOURCE_KEYS, SOURCE_KEYS):
                    continue
                if _validate_id(result, source.get("id"), f"{path}/id"):
                    if source["id"] in source_ids:
                        result.error("id.duplicate", f"{path}/id", "Duplicate source ID.")
                    source_ids.add(source["id"])
                _validate_enum(
                    result, source.get("kind"), f"{path}/kind", {"image", "text", "drawing", "measurement"}
                )
                _validate_text(result, source.get("uri"), f"{path}/uri", maximum=2000)
                _validate_text(
                    result, source.get("description"), f"{path}/description", maximum=1000
                )

    levels = document.get("levels")
    level_ids: set[str] = set()
    levels_by_id: dict[str, dict[str, Any]] = {}
    elevation_indices: set[int] = set()
    if not isinstance(levels, list) or not levels:
        result.error("array.required", "/levels", "Expected at least one level.")
        levels = []
    for i, level in enumerate(levels):
        path = f"/levels/{i}"
        if not _validate_keys(result, level, path, LEVEL_KEYS, LEVEL_KEYS):
            continue
        entity_id = level.get("id")
        if _validate_id(result, entity_id, f"{path}/id"):
            if entity_id in geometry_ids:
                duplicate_ids.add(entity_id)
                result.error("id.duplicate", f"{path}/id", "Geometry IDs must be globally unique.")
            geometry_ids[entity_id] = "level"
            level_ids.add(entity_id)
            levels_by_id[entity_id] = level
        _validate_text(result, level.get("name"), f"{path}/name", maximum=200)
        _validate_number(
            result, level.get("elevation_cm"), f"{path}/elevation_cm", minimum=-10000, maximum=10000
        )
        _validate_number(
            result,
            level.get("floor_thickness_cm"),
            f"{path}/floor_thickness_cm",
            minimum=0,
            maximum=300,
        )
        _validate_number(
            result,
            level.get("ceiling_height_cm"),
            f"{path}/ceiling_height_cm",
            minimum=0,
            maximum=2000,
            exclusive_minimum=True,
        )
        index = level.get("elevation_index")
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index <= 100):
            result.error("level.elevation-index", f"{path}/elevation_index", "Expected an integer 0–100.")
        elif index in elevation_indices:
            result.error(
                "level.elevation-index-duplicate",
                f"{path}/elevation_index",
                "Elevation indices must be unique.",
            )
        else:
            elevation_indices.add(index)
        _validate_bool(result, level.get("visible"), f"{path}/visible")
        _validate_bool(result, level.get("viewable"), f"{path}/viewable")
        _validate_evidence(result, level.get("evidence"), f"{path}/evidence")

    if isinstance(project, dict) and project.get("selected_level_id") not in level_ids:
        result.error(
            "reference.level",
            "/project/selected_level_id",
            "Selected level does not exist.",
        )

    sorted_levels = sorted(
        [level for level in levels if isinstance(level, dict) and _is_number(level.get("elevation_cm"))],
        key=lambda item: float(item["elevation_cm"]),
    )
    for current, following in zip(sorted_levels, sorted_levels[1:]):
        if _is_number(current.get("ceiling_height_cm")):
            expected_minimum = float(current["elevation_cm"]) + float(current["ceiling_height_cm"])
            if float(following["elevation_cm"]) + ENDPOINT_TOLERANCE_CM < expected_minimum:
                result.warning(
                    "level.vertical-overlap",
                    f"/levels/{levels.index(following)}/elevation_cm",
                    "This level begins below the preceding level's normal ceiling height.",
                )

    cameras = document.get("cameras")
    if not isinstance(cameras, list):
        result.error("type.array", "/cameras", "Expected an array.")
        cameras = []
    active_camera_count = 0
    for i, camera in enumerate(cameras):
        path = f"/cameras/{i}"
        if not _validate_keys(result, camera, path, CAMERA_KEYS, CAMERA_KEYS):
            continue
        entity_id = camera.get("id")
        if _validate_id(result, entity_id, f"{path}/id"):
            if entity_id in geometry_ids:
                duplicate_ids.add(entity_id)
                result.error(
                    "id.duplicate",
                    f"{path}/id",
                    "Geometry IDs must be globally unique.",
                )
            geometry_ids[entity_id] = "camera"
        _validate_text(result, camera.get("name"), f"{path}/name", maximum=200)
        _validate_enum(
            result,
            camera.get("mode"),
            f"{path}/mode",
            {"observer", "aerial"},
        )
        position_valid = _validate_camera_point(
            result, camera.get("position"), f"{path}/position"
        )
        target_valid = _validate_camera_point(
            result, camera.get("target"), f"{path}/target"
        )
        _validate_number(
            result,
            camera.get("field_of_view_degrees"),
            f"{path}/field_of_view_degrees",
            minimum=10,
            maximum=170,
        )
        _validate_enum(
            result,
            camera.get("lens"),
            f"{path}/lens",
            {"pinhole", "normal", "fisheye", "spherical"},
        )
        if _validate_bool(result, camera.get("active"), f"{path}/active"):
            active_camera_count += int(camera["active"])
        if camera.get("matches_source_id") not in source_ids:
            result.error(
                "reference.source",
                f"{path}/matches_source_id",
                "Camera source does not exist.",
            )
        _validate_evidence(result, camera.get("evidence"), f"{path}/evidence")
        if position_valid and target_valid:
            position = camera["position"]
            target = camera["target"]
            if all(
                abs(float(position[axis]) - float(target[axis])) <= EPSILON
                for axis in CAMERA_POINT_KEYS
            ):
                result.error(
                    "camera.zero-direction",
                    f"{path}/target",
                    "Camera position and target must differ.",
                )
    if active_camera_count > 1:
        result.error(
            "camera.multiple-active",
            "/cameras",
            "At most one camera may be active.",
        )

    walls = document.get("walls")
    if not isinstance(walls, list):
        result.error("type.array", "/walls", "Expected an array.")
        walls = []
    walls_by_id: dict[str, dict[str, Any]] = {}
    valid_walls: list[dict[str, Any]] = []
    for i, wall in enumerate(walls):
        path = f"/walls/{i}"
        required = WALL_KEYS - {"height_at_end_cm", "shape_params"}
        if not _validate_keys(result, wall, path, WALL_KEYS, required):
            continue
        entity_id = wall.get("id")
        if _validate_id(result, entity_id, f"{path}/id"):
            if entity_id in geometry_ids:
                duplicate_ids.add(entity_id)
                result.error("id.duplicate", f"{path}/id", "Geometry IDs must be globally unique.")
            geometry_ids[entity_id] = "wall"
            walls_by_id[entity_id] = wall
        if wall.get("level_id") not in level_ids:
            result.error("reference.level", f"{path}/level_id", "Wall level does not exist.")
        _validate_text(result, wall.get("name"), f"{path}/name", maximum=200)
        _validate_enum(
            result,
            wall.get("role"),
            f"{path}/role",
            {"exterior", "interior", "partition", "railing"},
        )
        if wall.get("role") == "railing":
            _validate_shape_params(
                result, "deco-railing", wall.get("shape_params"), f"{path}/shape_params"
            )
        elif wall.get("shape_params") is not None:
            result.error(
                "shape_params.unsupported",
                f"{path}/shape_params",
                "Only railing walls accept shape parameters.",
            )
        start_valid = _validate_point(result, wall.get("start"), f"{path}/start")
        end_valid = _validate_point(result, wall.get("end"), f"{path}/end")
        _validate_number(
            result,
            wall.get("thickness_cm"),
            f"{path}/thickness_cm",
            minimum=0,
            maximum=100,
            exclusive_minimum=True,
        )
        _validate_number(
            result,
            wall.get("height_cm"),
            f"{path}/height_cm",
            minimum=0,
            maximum=2000,
            exclusive_minimum=True,
        )
        if "height_at_end_cm" in wall:
            _validate_number(
                result,
                wall.get("height_at_end_cm"),
                f"{path}/height_at_end_cm",
                minimum=0,
                maximum=2000,
                exclusive_minimum=True,
            )
        _validate_color(result, wall.get("color"), f"{path}/color")
        _validate_evidence(result, wall.get("evidence"), f"{path}/evidence")
        if start_valid and end_valid:
            length = _wall_length(wall)
            if length <= EPSILON:
                result.error("wall.zero-length", path, "Wall start and end must differ.")
            else:
                valid_walls.append(wall)
                if _is_number(wall.get("thickness_cm")) and length <= float(wall["thickness_cm"]):
                    result.warning(
                        "wall.short",
                        path,
                        "Wall length is not greater than its thickness.",
                    )
        if wall.get("role") == "railing" and _is_number(wall.get("height_cm")):
            if float(wall["height_cm"]) > 150:
                result.warning(
                    "railing.tall",
                    f"{path}/height_cm",
                    "A railing proxy taller than 150 cm may be unintended.",
                )

    spaces = document.get("spaces")
    if not isinstance(spaces, list):
        result.error("type.array", "/spaces", "Expected an array.")
        spaces = []
    spaces_by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for i, space in enumerate(spaces):
        path = f"/spaces/{i}"
        if not _validate_keys(result, space, path, SPACE_KEYS, SPACE_KEYS):
            continue
        entity_id = space.get("id")
        if _validate_id(result, entity_id, f"{path}/id"):
            if entity_id in geometry_ids:
                duplicate_ids.add(entity_id)
                result.error("id.duplicate", f"{path}/id", "Geometry IDs must be globally unique.")
            geometry_ids[entity_id] = "space"
        level_id = space.get("level_id")
        if level_id not in level_ids:
            result.error("reference.level", f"{path}/level_id", "Space level does not exist.")
        else:
            spaces_by_level[level_id].append(space)
        _validate_text(result, space.get("name"), f"{path}/name", maximum=200)
        kind_valid = _validate_enum(
            result,
            space.get("kind"),
            f"{path}/kind",
            {"room", "balcony", "gallery", "terrace", "landing", "void"},
        )
        polygon = space.get("polygon")
        points: list[tuple[float, float]] = []
        if not isinstance(polygon, list) or len(polygon) < 3 or len(polygon) > 200:
            result.error("space.polygon-size", f"{path}/polygon", "Expected 3–200 points.")
        else:
            for j, point in enumerate(polygon):
                if _validate_point(result, point, f"{path}/polygon/{j}"):
                    points.append(_point_tuple(point))
            if len(points) == len(polygon):
                if any(_distance(points[j], points[(j + 1) % len(points)]) <= EPSILON for j in range(len(points))):
                    result.error("space.zero-edge", f"{path}/polygon", "Adjacent polygon points must differ.")
                if abs(_signed_area(points)) <= 1.0:
                    result.error("space.zero-area", f"{path}/polygon", "Space polygon area must exceed 1 cm².")
                if not _polygon_is_simple(points):
                    result.error("space.self-intersection", f"{path}/polygon", "Space polygon self-intersects.")
        for surface_name in ("floor", "ceiling"):
            surface = space.get(surface_name)
            surface_path = f"{path}/{surface_name}"
            if _validate_keys(result, surface, surface_path, SURFACE_KEYS, SURFACE_KEYS):
                _validate_bool(result, surface.get("enabled"), f"{surface_path}/enabled")
                _validate_color(result, surface.get("color"), f"{surface_path}/color")
                _validate_number(
                    result,
                    surface.get("shininess"),
                    f"{surface_path}/shininess",
                    minimum=0,
                    maximum=1,
                )
        if kind_valid and space.get("kind") == "void":
            if isinstance(space.get("floor"), dict) and space["floor"].get("enabled") is not False:
                result.error("void.floor", f"{path}/floor/enabled", "Void floor must be disabled.")
            if isinstance(space.get("ceiling"), dict) and space["ceiling"].get("enabled") is not False:
                result.error("void.ceiling", f"{path}/ceiling/enabled", "Void ceiling must be disabled.")
        _validate_bool(result, space.get("area_visible"), f"{path}/area_visible")
        _validate_evidence(result, space.get("evidence"), f"{path}/evidence")

    openings = document.get("openings")
    if not isinstance(openings, list):
        result.error("type.array", "/openings", "Expected an array.")
        openings = []
    opening_intervals: dict[str, list[tuple[float, float, str, str]]] = defaultdict(list)
    for i, opening in enumerate(openings):
        path = f"/openings/{i}"
        if not _validate_keys(
            result, opening, path, OPENING_KEYS, OPENING_KEYS - {"shape_params"}
        ):
            continue
        entity_id = opening.get("id")
        if _validate_id(result, entity_id, f"{path}/id"):
            if entity_id in geometry_ids:
                duplicate_ids.add(entity_id)
                result.error("id.duplicate", f"{path}/id", "Geometry IDs must be globally unique.")
            geometry_ids[entity_id] = "opening"
        wall = walls_by_id.get(opening.get("wall_id"))
        if wall is None:
            result.error("reference.wall", f"{path}/wall_id", "Opening wall does not exist.")
        _validate_text(result, opening.get("name"), f"{path}/name", maximum=200)
        opening_type_valid = _validate_enum(
            result,
            opening.get("type"),
            f"{path}/type",
            {"door", "sliding-door", "window", "opening"},
        )
        _validate_shape_params(
            result,
            opening.get("type"),
            opening.get("shape_params"),
            f"{path}/shape_params",
        )
        offset_valid = _validate_number(
            result,
            opening.get("center_offset_cm"),
            f"{path}/center_offset_cm",
            minimum=0,
            maximum=100000,
        )
        width_valid = _validate_number(
            result,
            opening.get("width_cm"),
            f"{path}/width_cm",
            minimum=0,
            maximum=5000,
            exclusive_minimum=True,
        )
        height_valid = _validate_number(
            result,
            opening.get("height_cm"),
            f"{path}/height_cm",
            minimum=0,
            maximum=2000,
            exclusive_minimum=True,
        )
        sill_valid = _validate_number(
            result,
            opening.get("sill_height_cm"),
            f"{path}/sill_height_cm",
            minimum=0,
            maximum=2000,
        )
        _validate_number(
            result,
            opening.get("depth_cm"),
            f"{path}/depth_cm",
            minimum=0,
            maximum=200,
            exclusive_minimum=True,
        )
        _validate_color(result, opening.get("color"), f"{path}/color")
        swing = opening.get("swing")
        if _validate_keys(result, swing, f"{path}/swing", SWING_KEYS, SWING_KEYS):
            _validate_enum(
                result, swing.get("hinge"), f"{path}/swing/hinge", {"left", "right", "none"}
            )
            _validate_enum(
                result, swing.get("direction"), f"{path}/swing/direction", {"in", "out", "none"}
            )
            _validate_number(
                result,
                swing.get("angle_degrees"),
                f"{path}/swing/angle_degrees",
                minimum=0,
                maximum=180,
            )
        _validate_evidence(result, opening.get("evidence"), f"{path}/evidence")
        if (
            opening_type_valid
            and opening.get("type") in {"door", "sliding-door"}
            and sill_valid
            and float(opening.get("sill_height_cm", 0)) > EPSILON
        ):
            result.warning(
                "opening.raised-door",
                f"{path}/sill_height_cm",
                "Door or passage has a nonzero sill.",
            )
        if wall is not None and offset_valid and width_valid:
            length = _wall_length(wall)
            center = float(opening["center_offset_cm"])
            half = float(opening["width_cm"]) / 2
            if center - half < -ENDPOINT_TOLERANCE_CM or center + half > length + ENDPOINT_TOLERANCE_CM:
                result.error("opening.outside-wall", path, "Opening does not fit completely on its wall.")
            else:
                opening_intervals[wall["id"]].append(
                    (center - half, center + half, str(entity_id), path)
                )
        if wall is not None and height_valid and sill_valid and _is_number(wall.get("height_cm")):
            if float(opening["height_cm"]) + float(opening["sill_height_cm"]) > float(
                wall["height_cm"]
            ) + ENDPOINT_TOLERANCE_CM:
                result.error("opening.above-wall", path, "Opening top is above its wall.")

    for wall_id, intervals in opening_intervals.items():
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1] - ENDPOINT_TOLERANCE_CM:
                result.error(
                    "opening.overlap",
                    current[3],
                    f"Opening overlaps {previous[2]!r} on wall {wall_id!r}.",
                )

    objects = document.get("objects")
    if not isinstance(objects, list):
        result.error("type.array", "/objects", "Expected an array.")
        objects = []
    for i, obj in enumerate(objects):
        path = f"/objects/{i}"
        required = OBJECT_KEYS - {"light_power", "light_color", "shape_params", "facing"}
        if not _validate_keys(result, obj, path, OBJECT_KEYS, required):
            continue
        entity_id = obj.get("id")
        if _validate_id(result, entity_id, f"{path}/id"):
            if entity_id in geometry_ids:
                duplicate_ids.add(entity_id)
                result.error("id.duplicate", f"{path}/id", "Geometry IDs must be globally unique.")
            geometry_ids[entity_id] = "object"
        level_id = obj.get("level_id")
        if level_id not in level_ids:
            result.error("reference.level", f"{path}/level_id", "Object level does not exist.")
        _validate_text(result, obj.get("name"), f"{path}/name", maximum=200)
        _validate_enum(
            result,
            obj.get("category"),
            f"{path}/category",
            {
                "cabinet",
                "counter",
                "table",
                "chair",
                "sofa",
                "bookcase",
                "fireplace",
                "light",
                "column",
                "decor",
                "fixture",
                "other",
            },
        )
        _validate_enum(result, obj.get("shape"), f"{path}/shape", BUILTIN_SHAPES)
        _validate_shape_params(
            result, obj.get("shape"), obj.get("shape_params"), f"{path}/shape_params"
        )
        point_valid = _validate_point(result, obj.get("center"), f"{path}/center")
        for dimension in ("width_cm", "depth_cm", "height_cm"):
            _validate_number(
                result,
                obj.get(dimension),
                f"{path}/{dimension}",
                minimum=0,
                maximum=10000 if dimension != "height_cm" else 5000,
                exclusive_minimum=True,
            )
        _validate_number(
            result,
            obj.get("elevation_cm"),
            f"{path}/elevation_cm",
            minimum=0,
            maximum=5000,
        )
        _validate_number(
            result,
            obj.get("angle_degrees"),
            f"{path}/angle_degrees",
            minimum=-360,
            maximum=360,
        )
        _validate_color(result, obj.get("color"), f"{path}/color")
        if "light_power" in obj:
            _validate_number(
                result,
                obj.get("light_power"),
                f"{path}/light_power",
                minimum=0,
                maximum=1,
            )
        if "light_color" in obj:
            _validate_color(result, obj.get("light_color"), f"{path}/light_color")
        if obj.get("category") != "light" and (
            "light_power" in obj or "light_color" in obj
        ):
            result.warning(
                "object.non-light-photometry",
                path,
                "Light power or color is ignored for a non-light object.",
            )
        _validate_bool(result, obj.get("movable"), f"{path}/movable")
        _validate_evidence(result, obj.get("evidence"), f"{path}/evidence")
        # Overhead shapes span the whole volume and are normally centred over a
        # void, which has no floor by definition, so the containment check does
        # not apply to them.
        if (
            point_valid
            and level_id in spaces_by_level
            and obj.get("shape") not in OVERHEAD_SHAPES
        ):
            floor_spaces = [
                space
                for space in spaces_by_level[level_id]
                if isinstance(space.get("floor"), dict) and space["floor"].get("enabled")
            ]
            if floor_spaces and not any(
                _point_in_polygon(
                    _point_tuple(obj["center"]),
                    [_point_tuple(point) for point in space["polygon"]],
                )
                for space in floor_spaces
                if isinstance(space.get("polygon"), list) and len(space["polygon"]) >= 3
            ):
                result.warning(
                    "object.outside-space",
                    f"{path}/center",
                    "Object center is outside every floor-enabled space on its level.",
                )

    if isinstance(project, dict) and _validate_id(ValidationResult(), project.get("id"), ""):
        geometry_ids.setdefault(project["id"], "project")

    if isinstance(provenance, dict):
        for collection_name in ("observations", "assumptions"):
            collection = provenance.get(collection_name)
            if not isinstance(collection, list):
                result.error(
                    "type.array", f"/provenance/{collection_name}", "Expected an array."
                )
                continue
            seen_item_ids: set[str] = set()
            for i, item in enumerate(collection):
                path = f"/provenance/{collection_name}/{i}"
                if not _validate_keys(
                    result, item, path, PROVENANCE_ITEM_KEYS, PROVENANCE_ITEM_KEYS
                ):
                    continue
                if _validate_id(result, item.get("id"), f"{path}/id"):
                    if item["id"] in seen_item_ids:
                        result.error("id.duplicate", f"{path}/id", "Duplicate provenance item ID.")
                    seen_item_ids.add(item["id"])
                if item.get("source_id") not in source_ids:
                    result.error(
                        "reference.source",
                        f"{path}/source_id",
                        "Provenance source does not exist.",
                    )
                _validate_text(result, item.get("statement"), f"{path}/statement", maximum=2000)
                _validate_number(
                    result,
                    item.get("confidence"),
                    f"{path}/confidence",
                    minimum=0,
                    maximum=1,
                )
                affects = item.get("affects")
                if not isinstance(affects, list) or not affects:
                    result.error("array.required", f"{path}/affects", "Expected at least one entity ID.")
                else:
                    for j, affected in enumerate(affects):
                        if affected not in geometry_ids:
                            result.error(
                                "reference.entity",
                                f"{path}/affects/{j}",
                                f"Affected entity {affected!r} does not exist.",
                            )

    _validate_camera_placement(result, document, cameras, spaces_by_level)
    _validate_enclosure(result, document, spaces_by_level)
    _validate_facing(result, document, spaces_by_level)

    endpoint_groups: dict[tuple[str, int, int], list[tuple[str, str]]] = defaultdict(list)
    for wall in valid_walls:
        for endpoint_name in ("start", "end"):
            x, y = _point_tuple(wall[endpoint_name])
            key = (
                wall["level_id"],
                round(x / ENDPOINT_TOLERANCE_CM),
                round(y / ENDPOINT_TOLERANCE_CM),
            )
            endpoint_groups[key].append((wall["id"], endpoint_name))
    for connected in endpoint_groups.values():
        if len(connected) > 2:
            wall_names = ", ".join(sorted(wall_id for wall_id, _ in connected))
            result.warning(
                "wall.junction-multi",
                "/walls",
                f"More than two walls share an endpoint ({wall_names}); SH3D joins only one peer.",
            )

    return result


def load_document(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in {"", "-0"} else "0"
    return str(value)


def _color_xml(color: str, alpha: str = "FF") -> str:
    """Serialize #RRGGBB as Sweet Home 3D's eight-digit ARGB hexadecimal."""
    return f"{alpha}{color[1:].upper()}"


def _set_attributes(element: ET.Element, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if value is not None:
            element.set(key, _fmt(value))


def _opening_position(
    opening: dict[str, Any], wall: dict[str, Any]
) -> tuple[float, float, float]:
    start = _point_tuple(wall["start"])
    end = _point_tuple(wall["end"])
    length = _distance(start, end)
    ux = (end[0] - start[0]) / length
    uy = (end[1] - start[1]) / length
    offset = float(opening["center_offset_cm"])
    return start[0] + ux * offset, start[1] + uy * offset, math.atan2(uy, ux)


def _joined_walls(document: dict[str, Any]) -> dict[tuple[str, str], str]:
    endpoint_groups: dict[tuple[str, int, int], list[tuple[str, str]]] = defaultdict(list)
    for wall in (wall for wall in document["walls"] if wall["role"] != "railing"):
        for endpoint_name in ("start", "end"):
            x, y = _point_tuple(wall[endpoint_name])
            key = (
                wall["level_id"],
                round(x / ENDPOINT_TOLERANCE_CM),
                round(y / ENDPOINT_TOLERANCE_CM),
            )
            endpoint_groups[key].append((wall["id"], endpoint_name))
    joins: dict[tuple[str, str], str] = {}
    for connected in endpoint_groups.values():
        if len(connected) == 2 and connected[0][0] != connected[1][0]:
            joins[connected[0]] = connected[1][0]
            joins[connected[1]] = connected[0][0]
    return joins


def _document_bounds(document: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for wall in document["walls"]:
        for endpoint_name in ("start", "end"):
            x, y = _point_tuple(wall[endpoint_name])
            xs.append(x)
            ys.append(y)
    for space in document["spaces"]:
        for point in space["polygon"]:
            x, y = _point_tuple(point)
            xs.append(x)
            ys.append(y)
    for obj in document["objects"]:
        x, y = _point_tuple(obj["center"])
        radius = math.hypot(float(obj["width_cm"]), float(obj["depth_cm"])) / 2
        xs.extend((x - radius, x + radius))
        ys.extend((y - radius, y + radius))
    if not xs or not ys:
        return 0, 0, 500, 400
    return min(xs), min(ys), max(xs), max(ys)


def _default_camera_attributes(document: dict[str, Any]) -> dict[str, Any]:
    """Return a comfortably framed southeast aerial view of the whole project."""
    min_x, min_y, max_x, max_y = _document_bounds(document)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    diagonal = max(400.0, math.hypot(max_x - min_x, max_y - min_y))
    highest_point = max(
        (
            float(level["elevation_cm"]) + float(level["ceiling_height_cm"])
            for level in document["levels"]
        ),
        default=300.0,
    )
    for wall in document["walls"]:
        level = next(
            level for level in document["levels"] if level["id"] == wall["level_id"]
        )
        wall_top = float(level["elevation_cm"]) + max(
            float(wall["height_cm"]), float(wall.get("height_at_end_cm", 0))
        )
        highest_point = max(highest_point, wall_top)

    horizontal_offset = diagonal * 0.9
    camera_x = center_x + horizontal_offset
    camera_y = center_y + horizontal_offset
    camera_z = max(highest_point * 1.35, diagonal * 1.05)
    target_z = highest_point * 0.3
    horizontal_distance = math.hypot(center_x - camera_x, center_y - camera_y)
    yaw = math.atan2(center_x - camera_x, -(center_y - camera_y))
    pitch = math.atan2(camera_z - target_z, horizontal_distance)
    return {
        "attribute": "topCamera",
        "lens": "PINHOLE",
        "x": camera_x,
        "y": camera_y,
        "z": camera_z,
        "yaw": yaw,
        "pitch": pitch,
        "fieldOfView": math.radians(63),
    }


def _camera_attributes_from_json(
    camera: dict[str, Any], attribute: str
) -> dict[str, Any]:
    position = camera["position"]
    target = camera["target"]
    delta_x = float(target["x_cm"]) - float(position["x_cm"])
    delta_y = float(target["y_cm"]) - float(position["y_cm"])
    horizontal_distance = math.hypot(delta_x, delta_y)
    # Sweet Home 3D's forward plan vector is (-sin(yaw), cos(yaw)):
    # yaw 0 faces plan-south and yaw pi faces plan-north.
    yaw = math.atan2(-delta_x, delta_y)
    pitch = math.atan2(
        float(position["z_cm"]) - float(target["z_cm"]),
        horizontal_distance,
    )
    return {
        "attribute": attribute,
        "name": camera["name"],
        "lens": camera["lens"].upper(),
        "x": position["x_cm"],
        "y": position["y_cm"],
        "z": position["z_cm"],
        "yaw": yaw,
        "pitch": pitch,
        "fieldOfView": math.radians(float(camera["field_of_view_degrees"])),
    }


def build_home_xml(document: dict[str, Any]) -> bytes:
    project = document["project"]
    models = model_registry(document)
    walls_by_id = {wall["id"]: wall for wall in document["walls"]}
    active_camera = next(
        (camera for camera in document["cameras"] if camera["active"]),
        None,
    )
    active_camera_name = (
        "observerCamera"
        if active_camera is not None and active_camera["mode"] == "observer"
        else "topCamera"
    )
    root = ET.Element("home")
    _set_attributes(
        root,
        {
            "version": SH3D_XML_VERSION,
            "name": f"{project['name']}.sh3d",
            "camera": active_camera_name,
            "selectedLevel": project["selected_level_id"],
            "wallHeight": project["default_wall_height_cm"],
        },
    )

    for name, value in (
        ("generator", "create-sh3d-floorplans"),
        ("floorplan.schemaVersion", document["schema_version"]),
        ("floorplan.projectId", project["id"]),
        ("floorplan.measurementStatus", document["provenance"]["measurement_status"]),
        ("floorplan.disclaimer", document["provenance"]["disclaimer"]),
    ):
        ET.SubElement(root, "property", {"name": name, "value": value})

    environment = {**ENVIRONMENT_DEFAULTS, **document.get("environment", {})}
    ET.SubElement(
        root,
        "environment",
        {
            "groundColor": _color_xml(environment["ground_color"], alpha="00"),
            "skyColor": _color_xml(environment["sky_color"], alpha="00"),
            "lightColor": _color_xml(environment["light_color"], alpha="00"),
            "ceillingLightColor": _color_xml(environment["ceiling_light_color"], alpha="00"),
            "wallsAlpha": _fmt(environment["walls_alpha"]),
            "allLevelsVisible": "true",
            "observerCameraElevationAdjusted": "true",
            "drawingMode": "FILL_AND_OUTLINE",
            "subpartSizeUnderLight": "0",
            "photoWidth": "1024",
            "photoHeight": "1024",
            "photoAspectRatio": "SQUARE_RATIO",
            "photoQuality": "3",
        },
    )

    camera = ET.SubElement(root, "camera")
    top_camera_attributes = (
        _camera_attributes_from_json(active_camera, "topCamera")
        if active_camera is not None and active_camera["mode"] == "aerial"
        else _default_camera_attributes(document)
    )
    _set_attributes(camera, top_camera_attributes)

    if active_camera is not None and active_camera["mode"] == "observer":
        observer = ET.SubElement(root, "observerCamera")
        _set_attributes(
            observer,
            _camera_attributes_from_json(active_camera, "observerCamera"),
        )
    for stored_camera in document["cameras"]:
        stored = ET.SubElement(
            root,
            "observerCamera" if stored_camera["mode"] == "observer" else "camera",
        )
        _set_attributes(
            stored,
            _camera_attributes_from_json(stored_camera, "storedCamera"),
        )

    for level in sorted(document["levels"], key=lambda item: item["elevation_index"]):
        ET.SubElement(
            root,
            "level",
            {
                "id": level["id"],
                "name": level["name"],
                "elevation": _fmt(level["elevation_cm"]),
                "floorThickness": _fmt(level["floor_thickness_cm"]),
                "height": _fmt(level["ceiling_height_cm"]),
                "elevationIndex": _fmt(level["elevation_index"]),
                "visible": _fmt(level["visible"]),
                "viewable": _fmt(level["viewable"]),
            },
        )

    for opening in document["openings"]:
        wall = walls_by_id[opening["wall_id"]]
        x, y, angle = _opening_position(opening, wall)
        if opening["type"] == "window":
            model_name = model_path_for(models, opening, "window")
        else:
            model_name = models[
                "opening" if opening["type"] == "opening" else "door"
            ]
        depth = float(opening["depth_cm"])
        element = ET.SubElement(root, "doorOrWindow")
        _set_attributes(
            element,
            {
                "id": opening["id"],
                "name": opening["name"],
                "level": wall["level_id"],
                "x": x,
                "y": y,
                "elevation": opening["sill_height_cm"],
                "angle": angle,
                "width": opening["width_cm"],
                "depth": depth,
                "height": opening["height_cm"],
                "model": model_name,
                "color": _color_xml(opening["color"]),
                "movable": False,
                "resizable": True,
                "deformable": True,
                "texturable": True,
                "modelCenteredAtOrigin": True,
                "backFaceShown": True,
                "dropOnTopElevation": 1,
                "wallThickness": min(1.0, float(wall["thickness_cm"]) / depth),
                "wallDistance": 0,
                "wallWidth": 1,
                "wallLeft": 0,
                "wallHeight": 1,
                "wallTop": 0,
                "wallCutOutOnBothSides": True,
                "widthDepthDeformable": True,
                "cutOutShape": "M0,0 v1 h1 v-1 z",
                "boundToWall": True,
            },
        )
        swing = opening["swing"]
        if opening["type"] == "door" and swing["hinge"] != "none" and swing["angle_degrees"] > 0:
            sign = 1 if swing["hinge"] == "left" else -1
            if swing["direction"] == "out":
                sign *= -1
            ET.SubElement(
                element,
                "sash",
                {
                    "xAxis": "0" if swing["hinge"] == "left" else "1",
                    "yAxis": "0.5",
                    "width": "1",
                    "startAngle": "0",
                    "endAngle": _fmt(sign * math.radians(float(swing["angle_degrees"]))),
                },
            )

    for obj in document["objects"]:
        element = ET.SubElement(
            root, "light" if obj["category"] == "light" else "pieceOfFurniture"
        )
        _set_attributes(
            element,
            {
                "id": obj["id"],
                "name": obj["name"],
                "level": obj["level_id"],
                "x": obj["center"]["x_cm"],
                "y": obj["center"]["y_cm"],
                "elevation": obj["elevation_cm"],
                "angle": math.radians(float(obj["angle_degrees"])),
                "width": obj["width_cm"],
                "depth": obj["depth_cm"],
                "height": obj["height_cm"],
                "model": model_path_for(models, obj, obj["shape"]),
                "color": _color_xml(obj["color"]),
                "movable": obj["movable"],
                "resizable": True,
                "deformable": True,
                "texturable": True,
                "modelCenteredAtOrigin": True,
                "backFaceShown": True,
                "dropOnTopElevation": 1,
                "power": obj.get("light_power", 0.65)
                if obj["category"] == "light"
                else None,
            },
        )
        if obj["category"] == "light":
            ET.SubElement(
                element,
                "lightSource",
                {
                    "x": "0.5",
                    "y": "0.5",
                    "z": "0.5",
                    "color": _color_xml(obj.get("light_color", "#FFF3D6")),
                    "diameter": "0.35",
                },
            )

    for railing in (wall for wall in document["walls"] if wall["role"] == "railing"):
        start = _point_tuple(railing["start"])
        end = _point_tuple(railing["end"])
        element = ET.SubElement(root, "pieceOfFurniture")
        _set_attributes(
            element,
            {
                "id": railing["id"],
                "name": railing["name"],
                "level": railing["level_id"],
                "x": (start[0] + end[0]) / 2,
                "y": (start[1] + end[1]) / 2,
                "elevation": 0,
                "angle": math.atan2(end[1] - start[1], end[0] - start[0]),
                "width": _distance(start, end),
                "depth": railing["thickness_cm"],
                "height": railing["height_cm"],
                "model": model_path_for(models, railing, "deco-railing"),
                "color": _color_xml(railing["color"]),
                "movable": False,
                "resizable": True,
                "deformable": True,
                "texturable": True,
                "modelCenteredAtOrigin": True,
                "backFaceShown": True,
                "dropOnTopElevation": 1,
            },
        )

    joins = _joined_walls(document)
    for wall in (wall for wall in document["walls"] if wall["role"] != "railing"):
        attributes: dict[str, Any] = {
            "id": wall["id"],
            "level": wall["level_id"],
            "xStart": wall["start"]["x_cm"],
            "yStart": wall["start"]["y_cm"],
            "xEnd": wall["end"]["x_cm"],
            "yEnd": wall["end"]["y_cm"],
            "height": wall["height_cm"],
            "thickness": wall["thickness_cm"],
            "pattern": "hatchUp",
            "topColor": _color_xml(wall["color"]),
            "leftSideColor": _color_xml(wall["color"]),
            "rightSideColor": _color_xml(wall["color"]),
        }
        if "height_at_end_cm" in wall:
            attributes["heightAtEnd"] = wall["height_at_end_cm"]
        if (wall["id"], "start") in joins:
            attributes["wallAtStart"] = joins[(wall["id"], "start")]
        if (wall["id"], "end") in joins:
            attributes["wallAtEnd"] = joins[(wall["id"], "end")]
        element = ET.SubElement(root, "wall")
        _set_attributes(element, attributes)

    for space in document["spaces"]:
        floor = space["floor"]
        ceiling = space["ceiling"]
        room = ET.SubElement(root, "room")
        _set_attributes(
            room,
            {
                "id": space["id"],
                "level": space["level_id"],
                "name": space["name"],
                "areaVisible": space["area_visible"],
                "floorVisible": floor["enabled"],
                "floorColor": _color_xml(floor["color"]),
                "floorShininess": floor["shininess"],
                "ceilingVisible": ceiling["enabled"],
                "ceilingColor": _color_xml(ceiling["color"]),
                "ceilingShininess": ceiling["shininess"],
                "ceilingFlat": True,
            },
        )
        for point in space["polygon"]:
            ET.SubElement(
                room,
                "point",
                {"x": _fmt(point["x_cm"]), "y": _fmt(point["y_cm"])},
            )

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _cuboid_lines(
    name: str,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    z0: float,
    z1: float,
    start_index: int,
) -> tuple[list[str], int]:
    vertices = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    lines = [f"g {name}", "s off"]
    lines.extend(f"v {x} {y} {z}" for x, y, z in vertices)
    local_faces = [
        (1, 4, 3, 2),
        (5, 6, 7, 8),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 4, 8, 7),
        (4, 1, 5, 8),
    ]
    for face in local_faces:
        indices = [start_index + index - 1 for index in face]
        # Emit each face once, wound outward. Emitting the reverse winding as
        # well puts two coplanar polygons at identical depth, which z-fights in
        # Sweet Home 3D's desktop renderer and bands every surface with stripes.
        # Two-sided visibility comes from backFaceShown="true" in the XML.
        lines.append("f " + " ".join(str(index) for index in indices))
    return lines, start_index + 8


def _box_obj() -> str:
    # Sweet Home 3D models use X=width, Y=height, Z=depth.
    lines, _ = _cuboid_lines("unit-box", -0.5, 0.5, 0, 1, -0.5, 0.5, 1)
    return "\n".join(["# procedural SH3D unit box", *lines, ""])


def _frame_obj(name: str, include_mullion: bool) -> str:
    parts = [
        ("left", -0.5, -0.42, 0, 1, -0.5, 0.5),
        ("right", 0.42, 0.5, 0, 1, -0.5, 0.5),
        ("bottom", -0.42, 0.42, 0, 0.08, -0.5, 0.5),
        ("top", -0.42, 0.42, 0.92, 1, -0.5, 0.5),
    ]
    if include_mullion:
        parts.extend(
            [
                ("center-vertical", -0.025, 0.025, 0.08, 0.92, -0.5, 0.5),
                ("center-horizontal", -0.42, 0.42, 0.475, 0.525, -0.5, 0.5),
            ]
        )
    lines = [f"# procedural SH3D {name}"]
    index = 1
    for part in parts:
        part_lines, index = _cuboid_lines(*part, index)
        lines.extend(part_lines)
    lines.append("")
    return "\n".join(lines)


def _cylinder_lines(
    name: str,
    radius_x: float,
    radius_z: float,
    y0: float,
    y1: float,
    segments: int,
    start_index: int,
) -> tuple[list[str], int]:
    lines = [f"g {name}", "s off"]
    for y in (y0, y1):
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            lines.append(
                f"v {radius_x * math.cos(angle):.8f} {y:.8f} "
                f"{radius_z * math.sin(angle):.8f}"
            )
    bottom_center = start_index + 2 * segments
    top_center = bottom_center + 1
    lines.extend([f"v 0 {y0:.8f} 0", f"v 0 {y1:.8f} 0"])

    def add_face(indices: list[int]) -> None:
        # Single winding only -- see _cuboid_lines on why the reverse winding
        # must not be emitted.
        lines.append("f " + " ".join(str(index) for index in indices))

    for i in range(segments):
        next_i = (i + 1) % segments
        b1, b2 = start_index + i, start_index + next_i
        t1, t2 = start_index + segments + i, start_index + segments + next_i
        add_face([b1, b2, t2, t1])
        add_face([bottom_center, b2, b1])
        add_face([top_center, t1, t2])
    return lines, start_index + 2 * segments + 2


def _cylinder_obj(segments: int = 32) -> str:
    part, _ = _cylinder_lines("unit-cylinder", 0.5, 0.5, 0, 1, segments, 1)
    lines = ["# procedural SH3D unit cylinder", *part, ""]
    return "\n".join(lines)


def _composite_obj(name: str, parts: list[tuple[Any, ...]]) -> str:
    lines = [f"# procedural SH3D {name}"]
    index = 1
    for part in parts:
        part_lines, index = _cuboid_lines(*part, index)
        lines.extend(part_lines)
    lines.append("")
    return "\n".join(lines)


def bookcase_shelf_elevations(
    height_cm: float, params: dict[str, float] | None = None
) -> list[float]:
    """Where book rows must sit, in centimetres above the piece's base.

    Books float unconvincingly between shelves unless their elevations come
    from the same arithmetic the generator uses, so it is exposed rather than
    documented and duplicated.
    """
    resolved = resolve_shape_params("bookcase", params)
    shelves = int(round(resolved["shelves"]))
    base = resolved["base_ratio"]
    return [
        round((base + (0.93 - base) * index / shelves) * height_cm, 1)
        for index in range(shelves)
    ]


def _bookcase_obj(params: dict[str, float]) -> str:
    shelves = max(1, int(round(params["shelves"])))
    base = min(max(params["base_ratio"], 0.02), 0.5)
    parts: list[tuple[Any, ...]] = [
        ("left-side", -0.5, -0.46, 0, 1, -0.5, 0.5),
        ("right-side", 0.46, 0.5, 0, 1, -0.5, 0.5),
        ("back", -0.48, 0.48, 0.01, 0.99, -0.49, -0.44),
        ("crown", -0.47, 0.47, 0.93, 0.985, -0.46, 0.49),
        # Solid cupboard plinth. Panelled bases are near-universal on a fitted
        # bookcase wall, and books running to the floor is the giveaway that
        # one was modelled as open shelving all the way down.
        ("base", -0.47, 0.47, 0.015, base, -0.46, 0.49),
        ("base-left-door", -0.45, -0.012, 0.05, base - 0.02, 0.47, 0.5),
        ("base-right-door", 0.012, 0.45, 0.05, base - 0.02, 0.47, 0.5),
    ]
    span = 0.93 - base
    for shelf_index in range(1, shelves):
        y = base + span * shelf_index / shelves
        parts.append((f"shelf-{shelf_index}", -0.45, 0.45, y, y + 0.02, -0.46, 0.47))
    for divider_index, x in enumerate((-0.16, 0.16)):
        parts.append(
            (f"divider-{divider_index}", x - 0.014, x + 0.014, base - 0.01, 0.94, -0.43, 0.46)
        )
    return _composite_obj("bookcase", parts)


def _books_obj() -> str:
    parts: list[tuple[Any, ...]] = []
    book_count = 18
    slot = 1 / book_count
    heights = (0.82, 0.96, 0.72, 0.9, 0.78, 1.0)
    for index in range(book_count):
        x0 = -0.5 + index * slot + 0.006
        x1 = -0.5 + (index + 1) * slot - 0.006
        height = heights[index % len(heights)]
        parts.append(
            (
                f"book-{index}",
                x0,
                x1,
                0,
                height,
                -0.5,
                0.5,
            )
        )
    return _composite_obj("books", parts)


def _cabinet_obj() -> str:
    return _composite_obj(
        "cabinet",
        [
            ("carcass", -0.5, 0.5, 0.04, 0.96, -0.5, 0.45),
            ("left-door", -0.48, -0.015, 0.08, 0.92, 0.42, 0.5),
            ("right-door", 0.015, 0.48, 0.08, 0.92, 0.42, 0.5),
            ("toe-kick", -0.46, 0.46, 0, 0.06, -0.42, 0.35),
        ],
    )


def _counter_obj() -> str:
    return _composite_obj(
        "counter",
        [
            ("base", -0.48, 0.48, 0, 0.9, -0.46, 0.47),
            ("countertop", -0.5, 0.5, 0.88, 1, -0.5, 0.5),
            ("left-door", -0.46, -0.015, 0.08, 0.82, 0.44, 0.5),
            ("right-door", 0.015, 0.46, 0.08, 0.82, 0.44, 0.5),
        ],
    )


# No two parts of a composite may share a plane: the exporter sets
# backFaceShown, so coplanar faces both get drawn and z-fight into stripes.
# Parts therefore overlap by this much instead of butting up against each
# other, which reads identically and cannot band.
_LAP = 0.02


def _sofa_obj(name: str = "sofa") -> str:
    return _composite_obj(
        name,
        [
            ("plinth", -0.44, 0.44, 0, 0.14, -0.4, 0.38),
            ("seat-base", -0.46, 0.46, 0.1, 0.38, -0.42, 0.4),
            ("seat-cushion", -0.37, 0.37, 0.34, 0.52, -0.22, 0.36),
            ("back", -0.44, 0.44, 0.35, 1, -0.48, -0.28),
            ("left-arm", -0.5, -0.39, 0.24, 0.74, -0.44, 0.42),
            ("right-arm", 0.39, 0.5, 0.24, 0.74, -0.44, 0.42),
        ],
    )


def _chair_obj(name: str = "chair", arms: bool = False) -> str:
    parts: list[tuple[Any, ...]] = [
        ("seat", -0.42, 0.42, 0.42, 0.54, -0.36, 0.36),
        # Inset from the seat's sides so the two do not share a side plane.
        ("back", -0.4, 0.4, 0.5, 1, -0.42, -0.3),
    ]
    for label, x, z in (
        ("front-left", -0.34, 0.28),
        ("front-right", 0.34, 0.28),
        ("back-left", -0.34, -0.28),
        ("back-right", 0.34, -0.28),
    ):
        parts.append((f"{label}-leg", x - 0.035, x + 0.035, 0, 0.44, z - 0.035, z + 0.035))
    if arms:
        parts.extend(
            [
                ("left-arm", -0.48, -0.38, 0.52, 0.74, -0.34, 0.34),
                ("right-arm", 0.38, 0.48, 0.52, 0.74, -0.34, 0.34),
            ]
        )
    return _composite_obj(name, parts)


def _armchair_obj() -> str:
    """Upholstered lounge chair: deep seat, proud arms, low plinth."""
    parts: list[tuple[Any, ...]] = [
        ("plinth", -0.34, 0.34, 0, 0.16, -0.36, 0.36),
        ("seat-base", -0.36, 0.36, 0.12, 0.46, -0.4, 0.4),
        ("cushion", -0.32, 0.32, 0.42, 0.58, -0.34, 0.38),
        ("back", -0.4, 0.4, 0.45, 1.0, -0.48, -0.32),
        ("left-arm", -0.5, -0.34, 0.4, 0.76, -0.44, 0.42),
        ("right-arm", 0.34, 0.5, 0.4, 0.76, -0.44, 0.42),
    ]
    for label, x, z in (
        ("front-left", -0.42, 0.34),
        ("front-right", 0.42, 0.34),
        ("back-left", -0.42, -0.4),
        ("back-right", 0.42, -0.4),
    ):
        parts.append((f"{label}-leg", x - 0.03, x + 0.03, 0, 0.47, z - 0.03, z + 0.03))
    return _composite_obj("armchair", parts)


def _dining_table_obj() -> str:
    return _composite_obj(
        "dining-table",
        [
            ("top", -0.5, 0.5, 0.88, 1, -0.5, 0.5),
            ("left-pedestal", -0.35, -0.22, 0.02, 0.9, -0.22, 0.22),
            ("right-pedestal", 0.22, 0.35, 0.02, 0.9, -0.22, 0.22),
            ("left-foot", -0.42, -0.15, 0, 0.08, -0.36, 0.36),
            ("right-foot", 0.15, 0.42, 0, 0.08, -0.36, 0.36),
        ],
    )


def _round_table_obj() -> str:
    lines = ["# procedural SH3D round table"]
    index = 1
    for args in (
        ("pedestal", 0.13, 0.13, 0.04, 0.9, 24),
        ("foot", 0.3, 0.3, 0, 0.09, 32),
        ("top", 0.5, 0.5, 0.88, 1, 40),
    ):
        part, index = _cylinder_lines(*args, index)
        lines.extend(part)
    lines.append("")
    return "\n".join(lines)


def _island_obj() -> str:
    lines = ["# procedural SH3D rounded island"]
    index = 1
    for args in (
        ("base", 0.43, 0.42, 0.13, 0, 0.9, 5),
        ("top", 0.5, 0.5, 0.16, 0.88, 1, 6),
    ):
        part, index = _rounded_rect_prism_lines(*args, index)
        lines.extend(part)
    lines.append("")
    return "\n".join(lines)


def _rounded_rect_prism_lines(
    name: str,
    half_x: float,
    half_z: float,
    radius: float,
    y0: float,
    y1: float,
    segments_per_corner: int,
    start_index: int,
) -> tuple[list[str], int]:
    radius = min(radius, half_x, half_z)
    centers = (
        (half_x - radius, half_z - radius, 0),
        (-half_x + radius, half_z - radius, 90),
        (-half_x + radius, -half_z + radius, 180),
        (half_x - radius, -half_z + radius, 270),
    )
    footprint: list[tuple[float, float]] = []
    for center_x, center_z, start_angle in centers:
        for step in range(segments_per_corner + 1):
            angle = math.radians(start_angle + 90 * step / segments_per_corner)
            footprint.append(
                (
                    center_x + radius * math.cos(angle),
                    center_z + radius * math.sin(angle),
                )
            )
    lines = [f"g {name}", "s off"]
    for y in (y0, y1):
        lines.extend(f"v {x:.8f} {y:.8f} {z:.8f}" for x, z in footprint)
    count = len(footprint)

    def add_face(indices: list[int]) -> None:
        # Single winding only -- see _cuboid_lines on why the reverse winding
        # must not be emitted.
        lines.append("f " + " ".join(str(index) for index in indices))

    bottom = [start_index + index for index in range(count)]
    top = [start_index + count + index for index in range(count)]
    add_face(bottom)
    add_face(top)
    for index in range(count):
        next_index = (index + 1) % count
        add_face(
            [
                start_index + index,
                start_index + next_index,
                start_index + count + next_index,
                start_index + count + index,
            ]
        )
    return lines, start_index + 2 * count


def _linear_pendant_obj() -> str:
    parts: list[tuple[Any, ...]] = [
        ("canopy", -0.48, 0.48, 0.92, 1, -0.34, 0.34),
        ("left-cable", -0.36, -0.345, 0.3, 0.94, -0.05, 0.05),
        ("right-cable", 0.345, 0.36, 0.3, 0.94, -0.05, 0.05),
        ("bar", -0.5, 0.5, 0.25, 0.33, -0.14, 0.14),
    ]
    for index, x in enumerate((-0.42, -0.25, -0.08, 0.08, 0.25, 0.42)):
        parts.append(
            (
                f"shade-{index}",
                x - 0.055,
                x + 0.055,
                0,
                0.24,
                -0.38,
                0.38,
            )
        )
    return _composite_obj("linear-pendant", parts)


def _annular_prism_lines(
    name: str,
    outer_x: float,
    outer_z: float,
    inner_x: float,
    inner_z: float,
    y0: float,
    y1: float,
    start_degrees: float,
    end_degrees: float,
    segments: int,
    start_index: int,
) -> tuple[list[str], int]:
    lines = [f"g {name}", "s off"]
    for y in (y0, y1):
        for radius_x, radius_z in ((outer_x, outer_z), (inner_x, inner_z)):
            for i in range(segments + 1):
                angle = math.radians(
                    start_degrees + (end_degrees - start_degrees) * i / segments
                )
                lines.append(
                    f"v {radius_x * math.cos(angle):.8f} {y:.8f} "
                    f"{radius_z * math.sin(angle):.8f}"
                )
    ring_count = segments + 1
    outer_bottom = start_index
    inner_bottom = outer_bottom + ring_count
    outer_top = inner_bottom + ring_count
    inner_top = outer_top + ring_count

    def add_face(indices: list[int]) -> None:
        # Single winding only -- see _cuboid_lines on why the reverse winding
        # must not be emitted.
        lines.append("f " + " ".join(str(index) for index in indices))

    for i in range(segments):
        add_face([outer_top + i, outer_top + i + 1, inner_top + i + 1, inner_top + i])
        add_face(
            [
                outer_bottom + i,
                inner_bottom + i,
                inner_bottom + i + 1,
                outer_bottom + i + 1,
            ]
        )
        add_face(
            [
                outer_bottom + i,
                outer_bottom + i + 1,
                outer_top + i + 1,
                outer_top + i,
            ]
        )
        add_face(
            [
                inner_bottom + i,
                inner_top + i,
                inner_top + i + 1,
                inner_bottom + i + 1,
            ]
        )
    for offset in (0, segments):
        add_face(
            [
                outer_bottom + offset,
                outer_top + offset,
                inner_top + offset,
                inner_bottom + offset,
            ]
        )
    return lines, start_index + 4 * ring_count


def _curved_banquette_obj() -> str:
    lines = ["# procedural SH3D curved banquette"]
    seat, index = _annular_prism_lines(
        "curved-seat", 0.5, 0.5, 0.28, 0.27, 0.08, 0.53, 48, 312, 28, 1
    )
    lines.extend(seat)
    # The back starts where the seat ends. Overlapping them would put two
    # coplanar outer walls at the same radius and z-fight into stripes.
    back, _ = _annular_prism_lines(
        "curved-back", 0.5, 0.5, 0.42, 0.41, 0.5, 1, 48, 312, 28, index
    )
    lines.extend(back)
    lines.append("")
    return "\n".join(lines)


def _chandelier_obj(params: dict[str, float]) -> str:
    """Tiered pendant whose chain length is separate from its body.

    A fixture's height in the plan has to span canopy to lowest drop, because
    that is the box Sweet Home 3D scales. Without a separate chain length the
    tiers can only ever sit directly under the ceiling, so hanging a light low
    in a double-height room meant stretching the tiers to match. ``drop_ratio``
    is the share of the total height taken by the chain above the body.
    """
    tiers = max(1, int(round(params["tiers"])))
    drop = min(max(params["drop_ratio"], 0.0), 0.9)
    body_top = 1.0 - drop
    # The lowest 8% of the body is the finial; the tiers fill the rest.
    finial = body_top * 0.08
    band = (body_top - finial) / tiers
    lines = ["# procedural SH3D tiered chandelier"]
    index = 1
    parts: list[tuple[Any, ...]] = [
        ("finial", 0.07, 0.07, 0.0, finial * 1.4, 16),
    ]
    for tier in range(tiers):
        # Widest at the top, tapering downward, like a stacked crystal fixture.
        radius = 0.5 * (0.42 + 0.58 * (tier + 1) / tiers)
        y0 = finial + band * tier
        parts.append((f"tier-{tier}", radius, radius, y0, y0 + band * 0.92, 36))
    if drop > 0.01:
        parts.append(("chain", 0.022, 0.022, body_top - band * 0.1, 1.0 - drop * 0.06, 12))
        parts.append(("canopy", 0.11, 0.11, 1.0 - drop * 0.07, 1.0, 20))
    else:
        parts.append(("canopy", 0.11, 0.11, body_top - 0.02, 1.0, 20))
    for args in parts:
        part, index = _cylinder_lines(*args, index)
        lines.extend(part)
    lines.append("")
    return "\n".join(lines)


def _fireplace_obj() -> str:
    """Chimneypiece whose opening is deep enough to show a slip set into it.

    The opening used to stop at 0.66 of the height, so a marble slip placed in
    it was almost entirely hidden behind the lintel and the fireplace read as a
    bare recess in plaster. Real chimneypieces run nearer 0.79.
    """
    return _composite_obj(
        "fireplace-surround",
        [
            ("left-jamb", -0.5, -0.33, 0.01, 0.84, -0.5, 0.5),
            ("right-jamb", 0.33, 0.5, 0.01, 0.84, -0.5, 0.5),
            ("lintel", -0.49, 0.49, 0.79, 0.87, -0.49, 0.49),
            ("mantel", -0.55, 0.55, 0.85, 0.94, -0.58, 0.58),
            ("overmantel", -0.42, 0.42, 0.92, 1, -0.46, 0.46),
            ("hearth", -0.46, 0.46, 0, 0.07, -0.65, 0.62),
        ],
    )


def _railing_obj() -> str:
    parts: list[tuple[Any, ...]] = [
        ("top-rail", -0.5, 0.5, 0.9, 1, -0.5, 0.5),
        ("bottom-rail", -0.5, 0.5, 0.06, 0.13, -0.5, 0.5),
    ]
    for post_index, x in enumerate(
        -0.48 + 0.096 * index for index in range(11)
    ):
        parts.append(
            (
                f"post-{post_index}",
                x - 0.014,
                x + 0.014,
                0.1,
                0.93,
                -0.5,
                0.5,
            )
        )
    return _composite_obj("railing", parts)


def _coffered_ceiling_obj(params: dict[str, float]) -> str:
    """Recessed ceiling grid, hung below the slab.

    Y=0 is the lowest point of the beams and Y=1 the recessed panel, so the
    model reads correctly when viewed from below.
    """
    columns = int(round(params["columns"]))
    rows = int(round(params["rows"]))
    beam = params["beam_ratio"]
    braces = params["cross_braces"] >= 0.5
    parts: list[tuple[Any, ...]] = [
        ("panel", -0.485, 0.485, 0.72, 1.0, -0.485, 0.485),
    ]
    # Perimeter cornice, then one beam per interior grid line each way.
    half = beam / 2
    for index in range(columns + 1):
        x = -0.5 + index / columns
        x0 = max(-0.5, x - half)
        x1 = min(0.5, x + half)
        parts.append((f"beam-x-{index}", x0, x1, 0.0, 0.74, -0.49, 0.49))
    for index in range(rows + 1):
        z = -0.5 + index / rows
        z0 = max(-0.5, z - half)
        z1 = min(0.5, z + half)
        # Stop 0.01 short of the cross beams' underside. Two solid beams that
        # cross would otherwise share a coplanar bottom face over every
        # intersection and z-fight into a speckled grid.
        parts.append((f"beam-z-{index}", -0.495, 0.495, 0.01, 0.76, z0, z1))
    if braces:
        # Thin diagonal-substitute cross inside each coffer, matching the
        # X-motif of panelled Art Deco ceilings without true diagonals.
        rib = beam / 3
        for column in range(columns):
            cx = -0.5 + (column + 0.5) / columns
            for row in range(rows):
                cz = -0.5 + (row + 0.5) / rows
                parts.append(
                    (
                        f"rib-x-{column}-{row}",
                        cx - rib,
                        cx + rib,
                        0.58,
                        0.78,
                        cz - 0.5 / rows + beam,
                        cz + 0.5 / rows - beam,
                    )
                )
                parts.append(
                    (
                        f"rib-z-{column}-{row}",
                        cx - 0.5 / columns + beam,
                        cx + 0.5 / columns - beam,
                        0.6,
                        0.8,
                        cz - rib,
                        cz + rib,
                    )
                )
    return _composite_obj("coffered-ceiling", parts)


def _wall_panel_obj(params: dict[str, float]) -> str:
    """Flat backing board with raised rectangular moulding frames."""
    columns = int(round(params["columns"]))
    rows = int(round(params["rows"]))
    border = params["border_ratio"]
    aspect = params.get("aspect", 1.0) or 1.0
    parts: list[tuple[Any, ...]] = [("back", -0.49, 0.49, 0.005, 0.995, -0.5, -0.18)]
    cell_w = 1.0 / columns
    cell_h = 1.0 / rows
    # One rail width in centimetres, expressed separately on each axis -- see
    # _paned_window_obj on why a single normalised value goes wrong on a panel
    # that is not square.
    rail = min(border * cell_w, 0.45 * cell_w)
    rail_y = min(rail * aspect, 0.45 * cell_h)
    for column in range(columns):
        x0 = -0.5 + column * cell_w
        x1 = x0 + cell_w
        for row in range(rows):
            y0 = row * cell_h
            y1 = y0 + cell_h
            tag = f"{column}-{row}"
            inset_x0, inset_x1 = x0 + rail, x1 - rail
            inset_y0, inset_y1 = y0 + rail_y, y1 - rail_y
            parts.extend(
                [
                    (f"left-{tag}", inset_x0 - rail, inset_x0, inset_y0 - rail_y * 0.5, inset_y1 + rail_y * 0.5, -0.19, 0.49),
                    (f"right-{tag}", inset_x1, inset_x1 + rail, inset_y0 - rail_y * 0.5, inset_y1 + rail_y * 0.5, -0.19, 0.49),
                    (f"bottom-{tag}", inset_x0 - rail * 0.5, inset_x1 + rail * 0.5, inset_y0 - rail_y, inset_y0, -0.2, 0.5),
                    (f"top-{tag}", inset_x0 - rail * 0.5, inset_x1 + rail * 0.5, inset_y1, inset_y1 + rail_y, -0.2, 0.5),
                ]
            )
    return _composite_obj("wall-panel", parts)


def _sconce_obj() -> str:
    return _composite_obj(
        "sconce",
        [
            ("backplate", -0.28, 0.28, 0.02, 0.98, -0.5, -0.28),
            ("body", -0.34, 0.34, 0.08, 0.92, -0.3, 0.32),
            ("cap", -0.4, 0.4, 0.89, 1.0, -0.33, 0.38),
            ("foot", -0.4, 0.4, 0.0, 0.11, -0.33, 0.38),
        ],
    )


def _deco_railing_obj(params: dict[str, float]) -> str:
    """Post-and-rail guard whose bays carry a radiating Art Deco fan."""
    bays = int(round(params["bays"]))
    rays = int(round(params["rays"]))
    parts: list[tuple[Any, ...]] = [
        ("top-rail", -0.5, 0.5, 0.9, 1.0, -0.5, 0.5),
        ("mid-rail", -0.5, 0.5, 0.52, 0.57, -0.5, 0.5),
        ("bottom-rail", -0.5, 0.5, 0.04, 0.12, -0.5, 0.5),
    ]
    bay_width = 1.0 / bays
    for bay in range(bays + 1):
        x = -0.5 + bay * bay_width
        # Posts are recessed slightly from the rail faces; flush posts would
        # share a coplanar front and back face with every rail they cross.
        parts.append((f"post-{bay}", x - 0.012, x + 0.012, 0.04, 1.0, -0.46, 0.46))
    # Each bay gets a fan of near-vertical bars that splay out with height,
    # approximating the sunburst grille with axis-aligned boxes.
    for bay in range(bays):
        centre = -0.5 + (bay + 0.5) * bay_width
        span = bay_width * 0.42
        for ray in range(rays):
            fraction = (ray / (rays - 1)) * 2 - 1 if rays > 1 else 0.0
            top_x = centre + fraction * span
            base_x = centre + fraction * span * 0.12
            steps = 4
            # Nudge each ray's thickness so two rays that cross near the hub
            # cannot land in the same plane.
            half_z = 0.22 - ray * 0.0015
            for step in range(steps):
                low = 0.12 + (0.52 - 0.12) * step / steps
                high = 0.12 + (0.52 - 0.12) * (step + 1) / steps + 0.004
                blend0 = step / steps
                blend1 = (step + 1) / steps
                x0 = base_x + (top_x - base_x) * blend0
                x1 = base_x + (top_x - base_x) * blend1
                lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
                pad = 0.008 + (ray * 5 + step) * 0.00012
                parts.append(
                    (
                        f"ray-{bay}-{ray}-{step}",
                        lo - pad,
                        hi + pad,
                        low,
                        high,
                        -half_z,
                        half_z,
                    )
                )
    return _composite_obj("deco-railing", parts)


def _rug_obj(params: dict[str, float]) -> str:
    """Floor mat with a raised border framing a recessed field.

    A rug is one colour like every other piece, so the border has to be a
    height change rather than a colour change: the field sits lower than the
    frame, and the shading difference is what makes it read as a rug rather
    than as a flat patch of floor.
    """
    border = params["border_ratio"]
    inset = min(border, 0.45)
    parts: list[tuple[Any, ...]] = [
        ("field", -0.5 + inset * 0.7, 0.5 - inset * 0.7, 0.01, 0.55,
         -0.5 + inset * 0.7, 0.5 - inset * 0.7),
        ("border-west", -0.5, -0.5 + inset, 0, 1, -0.5, 0.5),
        ("border-east", 0.5 - inset, 0.5, 0.002, 0.998, -0.5, 0.5),
        # North and south run under the side borders rather than butting into
        # them; a shared corner plane would z-fight along the rug's edge.
        ("border-north", -0.5 + inset * 0.5, 0.5 - inset * 0.5, 0.004, 0.996, -0.498, -0.5 + inset),
        ("border-south", -0.5 + inset * 0.5, 0.5 - inset * 0.5, 0.006, 0.994, 0.5 - inset, 0.498),
    ]
    return _composite_obj("rug", parts)


def _curtain_obj(params: dict[str, float]) -> str:
    """Hanging drape whose folds come from a wavy plan section.

    Each fold is a separate prism rather than one corrugated sheet, so the
    model stays closed. Folds narrow toward the top to suggest a gather.
    """
    folds = max(2, int(round(params["folds"])))
    parts: list[tuple[Any, ...]] = []
    slot = 1.0 / folds
    for index in range(folds):
        centre = -0.5 + (index + 0.5) * slot
        # Alternate the fold depth so the drape reads as pleated from any
        # angle instead of as a flat board.
        deep = 0.5 if index % 2 == 0 else 0.26
        parts.append(
            (
                f"fold-{index}",
                centre - slot * 0.46,
                centre + slot * 0.46,
                0,
                0.97,
                -deep,
                deep,
            )
        )
    parts.append(("heading", -0.5, 0.5, 0.95, 1.0, -0.48, 0.48))
    return _composite_obj("curtain", parts)


def _sunburst_obj(params: dict[str, float]) -> str:
    """Art Deco fan medallion: rays splaying up and out from a low hub.

    Built from stepped boxes for the same reason as the railing fan -- true
    rotated wedges would need arbitrary transforms the OBJ writer does not do.
    """
    rays = max(2, int(round(params["rays"])))
    parts: list[tuple[Any, ...]] = [("hub", -0.12, 0.12, 0, 0.16, -0.48, 0.48)]
    steps = 5
    for ray in range(rays):
        fraction = (ray / (rays - 1)) * 2 - 1 if rays > 1 else 0.0
        angle = fraction * math.radians(72)
        half_z = 0.5 - ray * 0.0015
        for step in range(steps):
            low = step / steps
            high = (step + 1) / steps + 0.01
            # Radial distance grows with the step; x follows the ray angle and
            # y follows its cosine, so the tips trace a fan.
            x0 = math.sin(angle) * low
            x1 = math.sin(angle) * high
            y0 = 0.1 + math.cos(angle) * low * 0.9
            y1 = 0.1 + math.cos(angle) * high * 0.9
            lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
            half = 0.016 + 0.020 * high + (ray * 5 + step) * 0.00025
            parts.append(
                (
                    f"ray-{ray}-{step}",
                    lo * 0.5 - half,
                    hi * 0.5 + half,
                    min(y0, y1),
                    max(y1, y0 + 0.02),
                    -half_z,
                    half_z,
                )
            )
    return _composite_obj("sunburst", parts)


def _crown_moulding_obj(params: dict[str, float]) -> str:
    """Stepped cornice running around the inside of a room's perimeter.

    Hollow: only the perimeter band is solid, so it can be dropped over a room
    without hiding the ceiling behind it.
    """
    steps = max(1, int(round(params["steps"])))
    width = params["band_ratio"]
    parts: list[tuple[Any, ...]] = []
    for step in range(steps):
        # Each course projects further into the room as it rises.
        depth = width * (step + 1) / steps
        y0 = step / steps
        # Courses overlap so consecutive ones never share a horizontal plane.
        y1 = min(1.0, (step + 1) / steps + 0.01)
        half = depth * 0.5
        # Every course would otherwise share the outer wall plane with every
        # other course; step them back by a hair each.
        edge = 0.5 - step * 0.001
        for tag, x0, x1, z0, z1 in (
            ("north", -edge, edge, -edge, -0.5 + depth),
            ("south", -edge, edge, 0.5 - depth, edge),
            ("west", -edge + half * 0.3, -0.5 + depth, -edge + half, edge - half),
            ("east", 0.5 - depth, edge - half * 0.3, -edge + half, edge - half),
        ):
            parts.append((f"{tag}-{step}", x0, x1, y0, y1, z0, z1))
    return _composite_obj("crown-moulding", parts)


def _paned_window_obj(params: dict[str, float]) -> str:
    """Window frame carrying an arbitrary grid of muntins.

    Frame and glazing bars are drawn at a constant real-world thickness. The
    unit cube is scaled independently on X and Y, so a fixed fraction would
    make the horizontal bars of a tall window several times thicker than its
    vertical ones. ``aspect`` (width / height) undoes that.
    """
    columns = int(round(params["columns"]))
    rows = int(round(params["rows"]))
    aspect = params.get("aspect", 1.0) or 1.0
    # Nominal thicknesses as a fraction of the window's *width*.
    frame = min(0.08, 0.5 / max(columns, 1))
    bar = min(0.014, 0.2 / max(columns, 1))
    # Vertical extents are divided by the aspect ratio so they come out the
    # same number of centimetres as the horizontal ones.
    frame_y = min(frame * aspect, 0.4 / max(rows, 1))
    bar_y = min(bar * aspect, 0.15 / max(rows, 1))
    inner_x0, inner_x1 = -0.5 + frame, 0.5 - frame
    inner_y0, inner_y1 = frame_y, 1 - frame_y
    parts: list[tuple[Any, ...]] = [
        ("left", -0.5, inner_x0, 0, 1, -0.5, 0.5),
        ("right", inner_x1, 0.5, 0, 1, -0.5, 0.5),
        ("bottom", inner_x0, inner_x1, 0, inner_y0, -0.5, 0.5),
        ("top", inner_x0, inner_x1, inner_y1, 1, -0.5, 0.5),
    ]
    span_x = inner_x1 - inner_x0
    span_y = inner_y1 - inner_y0
    for column in range(1, columns):
        x = inner_x0 + span_x * column / columns
        parts.append((f"muntin-v-{column}", x - bar, x + bar, inner_y0, inner_y1, -0.34, 0.34))
    for row in range(1, rows):
        y = inner_y0 + span_y * row / rows
        parts.append((f"muntin-h-{row}", inner_x0, inner_x1, y - bar_y, y + bar_y, -0.34, 0.34))
    return _composite_obj("paned-window", parts)


def _shape_model_name(shape: str, params: dict[str, float]) -> str:
    """Stable per-variant logical name; identical parameters reuse one OBJ."""
    if not params:
        return shape
    suffix = "-".join(
        f"{name}{_fmt(params[name]).replace('.', 'p').replace('-', 'm')}"
        for name in sorted(params)
    )
    return f"{shape}-{suffix}"


def _build_parametric_obj(shape: str, params: dict[str, float]) -> str:
    if shape == "coffered-ceiling":
        return _coffered_ceiling_obj(params)
    if shape == "wall-panel":
        return _wall_panel_obj(params)
    if shape == "deco-railing":
        return _deco_railing_obj(params)
    if shape == "chandelier":
        return _chandelier_obj(params)
    if shape == "bookcase":
        return _bookcase_obj(params)
    if shape == "rug":
        return _rug_obj(params)
    if shape == "curtain":
        return _curtain_obj(params)
    if shape == "sunburst":
        return _sunburst_obj(params)
    if shape == "crown-moulding":
        return _crown_moulding_obj(params)
    if shape == "window":
        return _paned_window_obj(params)
    raise KeyError(f"no parametric generator for shape {shape!r}")


BASE_MODEL_FILES = {
    "models/box.obj": _box_obj(),
    "models/cylinder.obj": _cylinder_obj(),
    "models/door.obj": _box_obj().replace("unit-box", "door-panel"),
    "models/window.obj": _frame_obj("window-frame", include_mullion=True),
    "models/opening.obj": _frame_obj("opening-frame", include_mullion=False),
    "models/sconce.obj": _sconce_obj(),
    "models/books.obj": _books_obj(),
    "models/cabinet.obj": _cabinet_obj(),
    "models/counter.obj": _counter_obj(),
    "models/sofa.obj": _sofa_obj(),
    "models/armchair.obj": _armchair_obj(),
    "models/chair.obj": _chair_obj(),
    "models/dining-table.obj": _dining_table_obj(),
    "models/round-table.obj": _round_table_obj(),
    "models/island.obj": _island_obj(),
    "models/linear-pendant.obj": _linear_pendant_obj(),
    "models/curved-banquette.obj": _curved_banquette_obj(),
    "models/fireplace.obj": _fireplace_obj(),
    "models/railing.obj": _railing_obj(),
}


# _finalize_obj first makes every group consistently outward-wound by the
# right-hand rule, then reverses it, because Sweet Home 3D culls the right-hand
# convention and renders those solids inside out. Confirmed twice in the
# application: right-hand outward shows every solid's interior, the reverse
# renders solid. Both steps are needed -- flipping without the consistency pass
# only fixes the models whose generator happened to agree with the cuboids.
# Set FLOORPLAN_FLIP_WINDING=0 to emit the right-hand convention for comparison.
FLIP_WINDING = os.environ.get("FLOORPLAN_FLIP_WINDING", "1") == "1"

MATERIAL_NAME = "shell"

# A neutral, fully diffuse white material. Sweet Home 3D tints a piece of
# furniture by overriding its model's material colour, so a model that declares
# no material cannot be recoloured and always renders white. Every procedural
# OBJ therefore ships with this MTL and a usemtl on each group.
MATERIAL_LIBRARY = "\n".join(
    [
        "# procedural SH3D default material",
        f"newmtl {MATERIAL_NAME}",
        "Ka 1.000 1.000 1.000",
        "Kd 1.000 1.000 1.000",
        "Ks 0.000 0.000 0.000",
        "Ns 0.0",
        "d 1.0",
        "illum 1",
        "",
    ]
)


def _group_signed_volume(
    vertices: list[tuple[float, float, float]], faces: list[list[int]]
) -> float:
    """Signed volume of a closed group; positive means wound outward.

    Works for non-convex solids such as the C-shaped banquette, where a
    centroid-based orientation test gives the wrong answer for inner walls.
    """
    total = 0.0
    for corners in faces:
        for offset in range(1, len(corners) - 1):
            a = vertices[corners[0] - 1]
            b = vertices[corners[offset] - 1]
            c = vertices[corners[offset + 1] - 1]
            total += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0
    return total


def _orient_group(faces: list[list[int]]) -> list[list[int]]:
    """Make every face of one closed group agree on which side is outside.

    Neighbouring faces are consistently wound when they traverse their shared
    edge in opposite directions, so orientation is propagated breadth-first
    across the group and any face reached the wrong way round is reversed. The
    revolved generators wind their caps opposite to their walls, which a
    whole-group flip cannot repair -- the result is a solid missing individual
    surfaces, such as a banquette with no seat.
    """
    edge_users: dict[tuple[int, int], list[int]] = defaultdict(list)
    for position, corners in enumerate(faces):
        for index in range(len(corners)):
            a, b = corners[index], corners[(index + 1) % len(corners)]
            edge_users[(min(a, b), max(a, b))].append(position)

    oriented = [list(face) for face in faces]
    settled = [False] * len(oriented)

    def directed_edges(position: int) -> set[tuple[int, int]]:
        corners = oriented[position]
        return {
            (corners[i], corners[(i + 1) % len(corners)])
            for i in range(len(corners))
        }

    for seed in range(len(oriented)):
        if settled[seed]:
            continue
        settled[seed] = True
        queue = [seed]
        while queue:
            position = queue.pop()
            for edge in directed_edges(position):
                key = (min(edge), max(edge))
                for neighbour in edge_users[key]:
                    if neighbour == position or settled[neighbour]:
                        continue
                    # Sharing the edge in the *same* direction means the
                    # neighbour faces the opposite way.
                    if edge in directed_edges(neighbour):
                        oriented[neighbour].reverse()
                    settled[neighbour] = True
                    queue.append(neighbour)
    return oriented


def _finalize_obj(obj_text: str, flip_winding: bool = False) -> str:
    """Rewrite a generated OBJ as consistently outward-wound triangles.

    Two things are normalised here so individual generators do not have to get
    them right:

    * **Orientation.** Each ``g`` group is a closed solid, so its signed volume
      says which way it is wound; groups that came out inward are reversed. The
      revolved generators (cylinder, rounded-rect and annular prisms) wind
      opposite to the cuboid ones, and an inward-wound solid is culled by Sweet
      Home 3D's renderer so you see straight through it into its interior.
    * **Triangles and normals.** Polygons are fan-triangulated and every face
      carries an explicit normal, which removes the importer's guesswork.
    """
    vertices: list[tuple[float, float, float]] = []
    for line in obj_text.splitlines():
        if line.startswith("v "):
            parts = line.split()
            vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))

    # First pass: which groups need reversing?
    group_faces: dict[str, list[list[int]]] = {}
    current = "default"
    for line in obj_text.splitlines():
        if line.startswith("g "):
            current = line.split(None, 1)[1].strip() if len(line.split()) > 1 else "default"
        elif line.startswith("f "):
            group_faces.setdefault(current, []).append(
                [int(token.split("/")[0]) for token in line.split()[1:]]
            )
    # Make each group internally consistent, then point it outward as a whole.
    # Faces are consumed in the order they appear, so the queue below stays in
    # step with the second pass over the file.
    oriented_faces: dict[str, list[list[int]]] = {}
    for name, faces in group_faces.items():
        oriented = _orient_group(faces)
        if _group_signed_volume(vertices, oriented) < 0:
            oriented = [list(reversed(face)) for face in oriented]
        oriented_faces[name] = oriented
    consumed: dict[str, int] = defaultdict(int)

    normals: list[tuple[float, float, float]] = []
    normal_index: dict[tuple[int, ...], int] = {}

    def register_normal(vector: tuple[float, float, float]) -> int:
        length = math.sqrt(sum(component * component for component in vector))
        unit = (0.0, 1.0, 0.0) if length < 1e-12 else tuple(
            component / length for component in vector
        )
        key = tuple(round(component * 1e6) for component in unit)
        if key not in normal_index:
            normals.append(unit)  # type: ignore[arg-type]
            normal_index[key] = len(normals)
        return normal_index[key]

    body: list[str] = []
    current = "default"
    for line in obj_text.splitlines():
        if line.startswith("g "):
            current = line.split(None, 1)[1].strip() if len(line.split()) > 1 else "default"
            body.append(line)
            continue
        if not line.startswith("f "):
            body.append(line)
            continue
        position = consumed[current]
        consumed[current] += 1
        corners = list(oriented_faces[current][position])
        if flip_winding:
            corners.reverse()
        if len(corners) < 3:
            continue
        a, b, c = (vertices[index - 1] for index in corners[:3])
        edge1 = tuple(b[axis] - a[axis] for axis in range(3))
        edge2 = tuple(c[axis] - a[axis] for axis in range(3))
        slot = register_normal(
            (
                edge1[1] * edge2[2] - edge1[2] * edge2[1],
                edge1[2] * edge2[0] - edge1[0] * edge2[2],
                edge1[0] * edge2[1] - edge1[1] * edge2[0],
            )
        )
        for offset in range(1, len(corners) - 1):
            triangle = (corners[0], corners[offset], corners[offset + 1])
            body.append("f " + " ".join(f"{index}//{slot}" for index in triangle))

    header = [f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in normals]
    return "\n".join([*header, *body, ""])


def _with_material(obj_text: str, mtl_name: str) -> str:
    """Attach the shared material library to a generated OBJ."""
    lines = obj_text.splitlines()
    output: list[str] = []
    inserted = False
    for line in lines:
        if not inserted and line.startswith("g "):
            output.append(f"mtllib {mtl_name}")
            inserted = True
        output.append(line)
        if line.startswith("g "):
            output.append(f"usemtl {MATERIAL_NAME}")
    if not inserted:
        output.insert(0, f"mtllib {mtl_name}")
    return "\n".join(output) + "\n"


def _model_specs(document: dict[str, Any]) -> dict[str, tuple[str, dict[str, float]]]:
    """Logical model name -> (shape, resolved parameters), in document order."""
    specs: dict[str, tuple[str, dict[str, float]]] = {}

    def add(shape: str, raw_params: Any, width: Any = None, height: Any = None) -> None:
        params = resolve_geometry_params(shape, raw_params, width, height)
        specs.setdefault(_shape_model_name(shape, params), (shape, params))

    for opening in document["openings"]:
        opening_type = opening["type"]
        if opening_type in SHAPE_PARAMETERS:
            add(
                opening_type,
                opening.get("shape_params"),
                opening.get("width_cm"),
                opening.get("height_cm"),
            )
        elif opening_type == "opening":
            add("opening", None)
        else:
            add("door", None)
    for obj in document["objects"]:
        add(obj["shape"], obj.get("shape_params"), obj.get("width_cm"), obj.get("height_cm"))
    for wall in document["walls"]:
        if wall["role"] == "railing":
            add("deco-railing", wall.get("shape_params"))
    return specs


def model_registry(document: dict[str, Any]) -> dict[str, str]:
    """Map each logical model name to its archive path.

    Sweet Home 3D resolves a model's siblings through its *containing
    directory*, so every model must live in a directory of its own. Putting
    them all in a shared ``models/`` folder makes the application treat them as
    one bundle and draw every piece of furniture with the same mesh -- the
    first OBJ it finds. This mirrors the layout Sweet Home 3D writes itself,
    e.g. ``1/bed/bed.obj``.
    """
    return {
        key: f"{index}/{key}/{key}.obj"
        for index, key in enumerate(_model_specs(document), start=1)
    }


def collect_model_files(document: dict[str, Any]) -> dict[str, str]:
    """Return the archive entries for every model this document references.

    Each model gets its own directory holding the OBJ and its material library,
    so no two models can be resolved as one another.
    """
    specs = _model_specs(document)
    registry = model_registry(document)
    files: dict[str, str] = {}
    for key, (shape, params) in specs.items():
        path = registry[key]
        directory = path.rsplit("/", 1)[0]
        geometry = (
            _build_parametric_obj(shape, params)
            if params
            else BASE_MODEL_FILES[f"models/{shape}.obj"]
        )
        mtl_name = f"{key}.mtl"
        files[path] = _with_material(_finalize_obj(geometry, FLIP_WINDING), mtl_name)
        files[f"{directory}/{mtl_name}"] = MATERIAL_LIBRARY
    return files


def model_path_for(
    registry: dict[str, str], entity: dict[str, Any], shape: str
) -> str:
    """Archive path the entity's XML should point at."""
    key = _shape_model_name(
        shape,
        resolve_geometry_params(
            shape,
            entity.get("shape_params"),
            entity.get("width_cm"),
            entity.get("height_cm"),
        ),
    )
    return registry[key]


def _zip_write_text(archive: zipfile.ZipFile, name: str, text: str | bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    payload = text if isinstance(text, bytes) else text.encode("utf-8")
    archive.writestr(info, payload)


def build_sh3d(document: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    xml_bytes = build_home_xml(document)
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w") as archive:
            _zip_write_text(archive, "Home.xml", xml_bytes)
            for name, content in collect_model_files(document).items():
                _zip_write_text(archive, name, content)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def inspect_sh3d(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            corrupt_name = archive.testzip()
            if corrupt_name:
                errors.append(f"CRC failure in {corrupt_name!r}.")
            if "Home.xml" not in names:
                errors.append("Home.xml is missing.")
                return {"valid": False, "errors": errors, "warnings": warnings, "counts": counts}
            if names[0] != "Home.xml":
                warnings.append("Home.xml is not the first archive entry.")
            root = ET.fromstring(archive.read("Home.xml"))
            if root.tag != "home":
                errors.append(f"Root element is {root.tag!r}, expected 'home'.")
            allowed_order = {
                "property": 0,
                "environment": 1,
                "camera": 2,
                "observerCamera": 2,
                "level": 3,
                "doorOrWindow": 4,
                "pieceOfFurniture": 4,
                "light": 4,
                "wall": 5,
                "room": 6,
            }
            order = -1
            for child in root:
                if child.tag not in allowed_order:
                    warnings.append(f"Unsupported root child {child.tag!r}.")
                    continue
                child_order = allowed_order[child.tag]
                if child_order < order:
                    errors.append(f"DTD order violation at root child {child.tag!r}.")
                order = max(order, child_order)
                counts[child.tag] = counts.get(child.tag, 0) + 1
            color_attributes = {
                "groundColor",
                "skyColor",
                "lightColor",
                "ceillingLightColor",
                "color",
                "topColor",
                "leftSideColor",
                "rightSideColor",
                "floorColor",
                "ceilingColor",
            }
            for element in root.iter():
                for attribute in color_attributes:
                    color = element.get(attribute)
                    if color is not None and not SH3D_COLOR_RE.fullmatch(color):
                        errors.append(
                            f"{element.tag} {attribute} has invalid SH3D ARGB color {color!r}."
                        )
            level_ids = {element.get("id") for element in root.findall("level")}
            for element in list(root.findall("wall")) + list(root.findall("room")):
                level_ref = element.get("level")
                if level_ref and level_ref not in level_ids:
                    errors.append(
                        f"{element.tag} {element.get('id')!r} refers to missing level {level_ref!r}."
                    )
            for element in (
                list(root.findall("doorOrWindow"))
                + list(root.findall("pieceOfFurniture"))
                + list(root.findall("light"))
            ):
                level_ref = element.get("level")
                if level_ref and level_ref not in level_ids:
                    errors.append(
                        f"{element.tag} {element.get('id')!r} refers to missing level {level_ref!r}."
                    )
                model_ref = element.get("model")
                if model_ref and model_ref not in names:
                    errors.append(
                        f"{element.tag} {element.get('id')!r} refers to missing model {model_ref!r}."
                    )
            return {
                "valid": not errors,
                "errors": errors,
                "warnings": warnings,
                "counts": counts,
                "entries": names,
                "xml_version": root.get("version"),
            }
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        return {
            "valid": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "warnings": warnings,
            "counts": counts,
        }


def _level_bounds(document: dict[str, Any], level_id: str) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for wall in document["walls"]:
        if wall["level_id"] == level_id:
            for endpoint in ("start", "end"):
                xs.append(float(wall[endpoint]["x_cm"]))
                ys.append(float(wall[endpoint]["y_cm"]))
    for space in document["spaces"]:
        if space["level_id"] == level_id:
            xs.extend(float(point["x_cm"]) for point in space["polygon"])
            ys.extend(float(point["y_cm"]) for point in space["polygon"])
    for obj in document["objects"]:
        if obj["level_id"] == level_id:
            xs.extend(
                [
                    float(obj["center"]["x_cm"]) - float(obj["width_cm"]) / 2,
                    float(obj["center"]["x_cm"]) + float(obj["width_cm"]) / 2,
                ]
            )
            ys.extend(
                [
                    float(obj["center"]["y_cm"]) - float(obj["depth_cm"]) / 2,
                    float(obj["center"]["y_cm"]) + float(obj["depth_cm"]) / 2,
                ]
            )
    if not xs:
        return 0, 0, 100, 100
    return min(xs), min(ys), max(xs), max(ys)


def _layout(document: dict[str, Any], scale: float = 0.75) -> tuple[list[dict[str, Any]], int, int]:
    margin = 45
    gap = 30
    panels: list[dict[str, Any]] = []
    offset_x = 0
    max_height = 0
    for level in sorted(document["levels"], key=lambda item: item["elevation_index"]):
        min_x, min_y, max_x, max_y = _level_bounds(document, level["id"])
        width = max(320, int(math.ceil((max_x - min_x) * scale + margin * 2)))
        height = max(320, int(math.ceil((max_y - min_y) * scale + margin * 2)))
        panels.append(
            {
                "level": level,
                "min_x": min_x,
                "min_y": min_y,
                "width": width,
                "height": height,
                "offset_x": offset_x,
                "offset_y": 0,
                "margin": margin,
                "scale": scale,
            }
        )
        offset_x += width + gap
        max_height = max(max_height, height)
    return panels, max(1, offset_x - gap), max_height


def _panel_xy(panel: dict[str, Any], point: tuple[float, float]) -> tuple[float, float]:
    return (
        panel["offset_x"] + panel["margin"] + (point[0] - panel["min_x"]) * panel["scale"],
        panel["offset_y"] + panel["margin"] + (point[1] - panel["min_y"]) * panel["scale"],
    )


def _space_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _rotated_rectangle(
    center: tuple[float, float], width: float, depth: float, angle_degrees: float
) -> list[tuple[float, float]]:
    angle = math.radians(angle_degrees)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    corners = [
        (-width / 2, -depth / 2),
        (width / 2, -depth / 2),
        (width / 2, depth / 2),
        (-width / 2, depth / 2),
    ]
    return [
        (
            center[0] + x * cos_a - y * sin_a,
            center[1] + x * sin_a + y * cos_a,
        )
        for x, y in corners
    ]


def render_svg(document: dict[str, Any], output_path: Path) -> None:
    panels, width, height = _layout(document)
    svg = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
        },
    )
    ET.SubElement(svg, "rect", {"width": "100%", "height": "100%", "fill": "#f5f2ec"})
    walls_by_id = {wall["id"]: wall for wall in document["walls"]}
    for panel in panels:
        level_id = panel["level"]["id"]
        ET.SubElement(
            svg,
            "rect",
            {
                "x": str(panel["offset_x"] + 1),
                "y": "1",
                "width": str(panel["width"] - 2),
                "height": str(panel["height"] - 2),
                "rx": "8",
                "fill": "#ffffff",
                "stroke": "#c8c1b7",
            },
        )
        title = ET.SubElement(
            svg,
            "text",
            {
                "x": str(panel["offset_x"] + 14),
                "y": "24",
                "font-family": "sans-serif",
                "font-size": "16",
                "font-weight": "bold",
                "fill": "#262626",
            },
        )
        title.text = (
            f"{panel['level']['name']} · elevation {panel['level']['elevation_cm']} cm"
        )
        for space in document["spaces"]:
            if space["level_id"] != level_id:
                continue
            raw_points = [_point_tuple(point) for point in space["polygon"]]
            points = [_panel_xy(panel, point) for point in raw_points]
            style = {
                "points": " ".join(f"{x:.2f},{y:.2f}" for x, y in points),
                "stroke-width": "1.5",
            }
            if space["kind"] == "void":
                style.update({"fill": "none", "stroke": "#b53a3a", "stroke-dasharray": "8 5"})
            else:
                style.update(
                    {
                        "fill": space["floor"]["color"],
                        "fill-opacity": "0.28",
                        "stroke": "#81786b",
                    }
                )
            ET.SubElement(svg, "polygon", style)
            cx, cy = _panel_xy(panel, _space_centroid(raw_points))
            label = ET.SubElement(
                svg,
                "text",
                {
                    "x": f"{cx:.2f}",
                    "y": f"{cy:.2f}",
                    "text-anchor": "middle",
                    "font-family": "sans-serif",
                    "font-size": "12",
                    "fill": "#343434",
                },
            )
            label.text = space["name"]
        for wall in document["walls"]:
            if wall["level_id"] != level_id:
                continue
            x1, y1 = _panel_xy(panel, _point_tuple(wall["start"]))
            x2, y2 = _panel_xy(panel, _point_tuple(wall["end"]))
            ET.SubElement(
                svg,
                "line",
                {
                    "x1": f"{x1:.2f}",
                    "y1": f"{y1:.2f}",
                    "x2": f"{x2:.2f}",
                    "y2": f"{y2:.2f}",
                    "stroke": wall["color"],
                    "stroke-width": f"{max(2, float(wall['thickness_cm']) * panel['scale']):.2f}",
                    "stroke-linecap": "square",
                },
            )
        for opening in document["openings"]:
            wall = walls_by_id[opening["wall_id"]]
            if wall["level_id"] != level_id:
                continue
            x, y, angle = _opening_position(opening, wall)
            half = float(opening["width_cm"]) / 2
            dx, dy = math.cos(angle) * half, math.sin(angle) * half
            x1, y1 = _panel_xy(panel, (x - dx, y - dy))
            x2, y2 = _panel_xy(panel, (x + dx, y + dy))
            erase_width = max(3, float(wall["thickness_cm"]) * panel["scale"] + 2)
            ET.SubElement(
                svg,
                "line",
                {
                    "x1": f"{x1:.2f}",
                    "y1": f"{y1:.2f}",
                    "x2": f"{x2:.2f}",
                    "y2": f"{y2:.2f}",
                    "stroke": "#ffffff",
                    "stroke-width": f"{erase_width:.2f}",
                },
            )
            ET.SubElement(
                svg,
                "line",
                {
                    "x1": f"{x1:.2f}",
                    "y1": f"{y1:.2f}",
                    "x2": f"{x2:.2f}",
                    "y2": f"{y2:.2f}",
                    "stroke": "#2489a8" if opening["type"] == "window" else "#a56528",
                    "stroke-width": "3",
                },
            )
        for obj in document["objects"]:
            if obj["level_id"] != level_id:
                continue
            center = _point_tuple(obj["center"])
            cx, cy = _panel_xy(panel, center)
            if obj["shape"] in OUTLINE_ONLY_SHAPES:
                corners = _rotated_rectangle(
                    center,
                    float(obj["width_cm"]),
                    float(obj["depth_cm"]),
                    float(obj["angle_degrees"]),
                )
                ET.SubElement(
                    svg,
                    "polygon",
                    {
                        "points": " ".join(
                            "{:.2f},{:.2f}".format(*_panel_xy(panel, corner))
                            for corner in corners
                        ),
                        "fill": "none",
                        "stroke": obj["color"],
                        "stroke-width": "1.5",
                        "stroke-dasharray": "7 5",
                    },
                )
                continue
            if obj["shape"] in {
                "cylinder",
                "round-table",
                "island",
                "curved-banquette",
                "chandelier",
            }:
                ET.SubElement(
                    svg,
                    "ellipse",
                    {
                        "cx": f"{cx:.2f}",
                        "cy": f"{cy:.2f}",
                        "rx": f"{float(obj['width_cm']) * panel['scale'] / 2:.2f}",
                        "ry": f"{float(obj['depth_cm']) * panel['scale'] / 2:.2f}",
                        "fill": obj["color"],
                        "fill-opacity": "0.62",
                        "stroke": "#3c3c3c",
                    },
                )
            else:
                corners = _rotated_rectangle(
                    center,
                    float(obj["width_cm"]),
                    float(obj["depth_cm"]),
                    float(obj["angle_degrees"]),
                )
                points = [_panel_xy(panel, point) for point in corners]
                ET.SubElement(
                    svg,
                    "polygon",
                    {
                        "points": " ".join(f"{x:.2f},{y:.2f}" for x, y in points),
                        "fill": obj["color"],
                        "fill-opacity": "0.62",
                        "stroke": "#3c3c3c",
                    },
                )
            # Which way the piece faces, drawn so a backwards sofa is visible
            # in the plan instead of only in a 3D render.
            if obj["shape"] in DIRECTIONAL_SHAPES:
                vector = facing_vector(float(obj["angle_degrees"]), obj["shape"])
                reach = float(obj["depth_cm"]) / 2 + 18
                tip = _panel_xy(
                    panel,
                    (center[0] + vector[0] * reach, center[1] + vector[1] * reach),
                )
                ET.SubElement(
                    svg,
                    "line",
                    {
                        "x1": f"{cx:.2f}",
                        "y1": f"{cy:.2f}",
                        "x2": f"{tip[0]:.2f}",
                        "y2": f"{tip[1]:.2f}",
                        "stroke": "#c0392b",
                        "stroke-width": "2",
                    },
                )
                ET.SubElement(
                    svg,
                    "circle",
                    {
                        "cx": f"{tip[0]:.2f}",
                        "cy": f"{tip[1]:.2f}",
                        "r": "3.2",
                        "fill": "#c0392b",
                    },
                )
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))


def _lighten(color: str, amount: float = 0.68) -> tuple[int, int, int]:
    r, g, b = _rgb(color)
    return (
        round(r + (255 - r) * amount),
        round(g + (255 - g) * amount),
        round(b + (255 - b) * amount),
    )


def render_png(document: dict[str, Any], output_path: Path) -> str | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return "Pillow is not installed; PNG preview was skipped."

    panels, width, height = _layout(document)
    image = Image.new("RGB", (width, height), "#f5f2ec")
    draw = ImageDraw.Draw(image)
    walls_by_id = {wall["id"]: wall for wall in document["walls"]}
    for panel in panels:
        level_id = panel["level"]["id"]
        x0 = panel["offset_x"] + 1
        draw.rounded_rectangle(
            (x0, 1, x0 + panel["width"] - 2, panel["height"] - 2),
            radius=8,
            fill="white",
            outline="#c8c1b7",
        )
        draw.text(
            (panel["offset_x"] + 14, 10),
            f"{panel['level']['name']} · elevation {panel['level']['elevation_cm']} cm",
            fill="#262626",
        )
        for space in document["spaces"]:
            if space["level_id"] != level_id:
                continue
            raw_points = [_point_tuple(point) for point in space["polygon"]]
            points = [_panel_xy(panel, point) for point in raw_points]
            if space["kind"] == "void":
                draw.line(points + [points[0]], fill="#b53a3a", width=2, joint="curve")
            else:
                draw.polygon(
                    points,
                    fill=_lighten(space["floor"]["color"]),
                    outline="#81786b",
                )
            cx, cy = _panel_xy(panel, _space_centroid(raw_points))
            draw.text((cx, cy), space["name"], fill="#343434", anchor="mm")
        for wall in document["walls"]:
            if wall["level_id"] != level_id:
                continue
            start = _panel_xy(panel, _point_tuple(wall["start"]))
            end = _panel_xy(panel, _point_tuple(wall["end"]))
            draw.line(
                (start, end),
                fill=wall["color"],
                width=max(2, round(float(wall["thickness_cm"]) * panel["scale"])),
            )
        for opening in document["openings"]:
            wall = walls_by_id[opening["wall_id"]]
            if wall["level_id"] != level_id:
                continue
            x, y, angle = _opening_position(opening, wall)
            half = float(opening["width_cm"]) / 2
            delta = math.cos(angle) * half, math.sin(angle) * half
            start = _panel_xy(panel, (x - delta[0], y - delta[1]))
            end = _panel_xy(panel, (x + delta[0], y + delta[1]))
            draw.line(
                (start, end),
                fill="white",
                width=max(3, round(float(wall["thickness_cm"]) * panel["scale"]) + 2),
            )
            draw.line(
                (start, end),
                fill="#2489a8" if opening["type"] == "window" else "#a56528",
                width=3,
            )
        for obj in document["objects"]:
            if obj["level_id"] != level_id:
                continue
            center = _point_tuple(obj["center"])
            if obj["shape"] in OUTLINE_ONLY_SHAPES:
                corners = [
                    _panel_xy(panel, corner)
                    for corner in _rotated_rectangle(
                        center,
                        float(obj["width_cm"]),
                        float(obj["depth_cm"]),
                        float(obj["angle_degrees"]),
                    )
                ]
                draw.line(corners + [corners[0]], fill=_rgb(obj["color"]), width=2)
            elif obj["shape"] in {
                "cylinder",
                "round-table",
                "island",
                "curved-banquette",
                "chandelier",
            }:
                cx, cy = _panel_xy(panel, center)
                half_w = float(obj["width_cm"]) * panel["scale"] / 2
                half_d = float(obj["depth_cm"]) * panel["scale"] / 2
                draw.ellipse(
                    (cx - half_w, cy - half_d, cx + half_w, cy + half_d),
                    fill=_lighten(obj["color"], 0.35),
                    outline="#3c3c3c",
                )
            else:
                corners = _rotated_rectangle(
                    center,
                    float(obj["width_cm"]),
                    float(obj["depth_cm"]),
                    float(obj["angle_degrees"]),
                )
                draw.polygon(
                    [_panel_xy(panel, point) for point in corners],
                    fill=_lighten(obj["color"], 0.35),
                    outline="#3c3c3c",
                )
            # See render_svg: a facing arrow makes a backwards piece visible in
            # the plan rather than only in a 3D render.
            if obj["shape"] in DIRECTIONAL_SHAPES:
                vector = facing_vector(float(obj["angle_degrees"]), obj["shape"])
                reach = float(obj["depth_cm"]) / 2 + 18
                start = _panel_xy(panel, center)
                tip = _panel_xy(
                    panel,
                    (center[0] + vector[0] * reach, center[1] + vector[1] * reach),
                )
                draw.line([start, tip], fill=(192, 57, 43), width=2)
                draw.ellipse(
                    (tip[0] - 3, tip[1] - 3, tip[0] + 3, tip[1] + 3),
                    fill=(192, 57, 43),
                )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _print_result(report: dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=False))


def command_validate(args: argparse.Namespace) -> int:
    try:
        document = load_document(args.input)
    except (OSError, json.JSONDecodeError) as exc:
        report = {
            "valid": False,
            "error_count": 1,
            "warning_count": 0,
            "errors": [{"code": "json.read", "path": "", "message": str(exc)}],
            "warnings": [],
        }
        if args.report:
            _write_json(args.report, report)
        _print_result(report)
        return 2
    result = validate_document(document)
    report = result.as_dict()
    if args.report:
        _write_json(args.report, report)
    _print_result(report)
    return 0 if result.valid else 2


def command_build(args: argparse.Namespace) -> int:
    try:
        document = load_document(args.input)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read {args.input}: {exc}", file=sys.stderr)
        return 2
    result = validate_document(document)
    report = result.as_dict()
    report["input"] = str(args.input)
    if not result.valid:
        if args.report:
            _write_json(args.report, report)
        _print_result(report)
        return 2
    build_sh3d(document, args.output)
    report["output"] = str(args.output)
    if args.svg:
        render_svg(document, args.svg)
        report["svg"] = str(args.svg)
    if args.png:
        png_warning = render_png(document, args.png)
        if png_warning:
            report["warnings"].append(
                {"code": "preview.png-skipped", "path": str(args.png), "message": png_warning}
            )
            report["warning_count"] = len(report["warnings"])
        else:
            report["png"] = str(args.png)
    report["archive_inspection"] = inspect_sh3d(args.output)
    if not report["archive_inspection"]["valid"]:
        report["valid"] = False
        report["errors"].append(
            {
                "code": "archive.invalid",
                "path": str(args.output),
                "message": "Generated archive failed inspection.",
            }
        )
        report["error_count"] = len(report["errors"])
    if args.report:
        _write_json(args.report, report)
    _print_result(report)
    return 0 if report["valid"] else 2


def command_inspect(args: argparse.Namespace) -> int:
    report = inspect_sh3d(args.input)
    _print_result(report)
    return 0 if report["valid"] else 2


def fit_camera(
    document: dict[str, Any],
    wall_id: str,
    fill: float,
    eye_height_cm: float,
    field_of_view_degrees: float,
) -> dict[str, Any]:
    """Solve for an eye-level view that frames ``wall_id`` at a given width.

    ``fill`` is the fraction of the frame width the wall should span. Distance
    follows from ``length / (2 * D * tan(fov / 2)) == fill``; the camera is then
    placed on the wall's inward normal and clamped to stay inside the plan.
    """
    wall = next((w for w in document["walls"] if w["id"] == wall_id), None)
    if wall is None:
        raise KeyError(f"no wall with id {wall_id!r}")
    start = _point_tuple(wall["start"])
    end = _point_tuple(wall["end"])
    length = _distance(start, end)
    midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    half_angle = math.radians(field_of_view_degrees) / 2
    distance = length / (2 * fill * math.tan(half_angle))

    # Both wall normals; keep the one that points into the plan.
    dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    spaces_by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for space in document["spaces"]:
        spaces_by_level[space["level_id"]].append(space)
    polygons = _floor_polygons(spaces_by_level)
    inward = None
    for normal in ((-dy, dx), (dy, -dx)):
        probe = (midpoint[0] + normal[0] * 30, midpoint[1] + normal[1] * 30)
        if any(_point_in_polygon(probe, polygon) for polygon in polygons):
            inward = normal
            break
    if inward is None:
        raise ValueError(f"could not find the interior side of wall {wall_id!r}")

    def eye_at(d: float) -> tuple[float, float]:
        return (midpoint[0] + inward[0] * d, midpoint[1] + inward[1] * d)

    # Walk back toward the wall until the viewpoint is inside the plan, so the
    # camera never ends up beyond the far wall.
    placed = distance
    while placed > 40 and not any(
        _point_in_polygon(eye_at(placed), polygon) for polygon in polygons
    ):
        placed -= 10
    clamped = placed < distance - 1e-6
    achieved_fov = (
        math.degrees(2 * math.atan(length / (2 * fill * placed)))
        if placed > 0
        else field_of_view_degrees
    )
    eye = eye_at(placed)
    wall_height = float(wall["height_cm"])
    return {
        "wall_id": wall_id,
        "wall_length_cm": round(length, 1),
        "requested_distance_cm": round(distance, 1),
        "placed_distance_cm": round(placed, 1),
        "clamped_to_plan": clamped,
        "field_of_view_degrees": round(
            achieved_fov if clamped else field_of_view_degrees, 1
        ),
        "camera": {
            "position": {
                "x_cm": round(eye[0], 1),
                "y_cm": round(eye[1], 1),
                "z_cm": round(eye_height_cm, 1),
            },
            "target": {
                "x_cm": round(midpoint[0], 1),
                "y_cm": round(midpoint[1], 1),
                "z_cm": round(min(wall_height * 0.45, eye_height_cm * 0.75), 1),
            },
            "field_of_view_degrees": round(
                achieved_fov if clamped else field_of_view_degrees, 1
            ),
        },
        "note": (
            "Distance was clamped so the camera stays inside the plan; the field "
            "of view was widened to keep the requested framing. Widening the lens "
            "exaggerates near objects -- if that looks wrong, the room is probably "
            "too shallow rather than the lens too narrow."
            if clamped
            else "Camera fits inside the plan at the requested field of view."
        ),
    }


def command_camera_fit(args: argparse.Namespace) -> int:
    document = load_document(args.input)
    validation = validate_document(document)
    if not validation.valid:
        _print_result(validation.as_dict())
        return 2
    try:
        report = fit_camera(
            document, args.wall, args.fill, args.eye, args.field_of_view
        )
    except (KeyError, ValueError) as error:
        print(json.dumps({"valid": False, "errors": [str(error)]}, indent=2))
        return 2
    if args.apply:
        camera = next(
            (c for c in document["cameras"] if c["id"] == args.apply), None
        )
        if camera is None:
            print(json.dumps({"valid": False, "errors": [f"no camera {args.apply!r}"]}))
            return 2
        camera.update(report["camera"])
        _write_json(args.input, document)
        report["applied_to"] = args.apply
    print(json.dumps(report, indent=2))
    return 0


def coplanar_overlaps(shape: str, params: dict[str, float]) -> list[dict[str, Any]]:
    """Report pairs of parts that share a plane and overlap inside it.

    The exporter sets backFaceShown, so nothing is culled and two faces in one
    plane both get drawn -- they z-fight and band the surface with stripes.
    Parts that merely touch are enough to trigger it, so a part stacked exactly
    on another is a defect even though the model looks correct in wireframe.

    Only pairs from *different* parts are reported: a cylinder's end cap is a
    triangle fan whose triangles legitimately share one plane.
    """
    body = (
        _build_parametric_obj(shape, params)
        if params
        else BASE_MODEL_FILES[f"models/{shape}.obj"]
    )
    vertices: list[tuple[float, float, float]] = []
    planes: dict[tuple[int, float], list[tuple[str, tuple[float, float, float, float]]]] = {}
    group = "default"
    for line in body.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            vertices.append(tuple(float(value) for value in parts[1:4]))
        elif parts[0] == "g":
            group = parts[1] if len(parts) > 1 else "default"
        elif parts[0] == "f":
            points = [vertices[int(token.split("/")[0]) - 1] for token in parts[1:]]
            for axis in range(3):
                levels = {round(point[axis], 6) for point in points}
                if len(levels) != 1:
                    continue
                u, v = [a for a in range(3) if a != axis]
                planes.setdefault((axis, levels.pop()), []).append(
                    (
                        group,
                        (
                            min(p[u] for p in points),
                            max(p[u] for p in points),
                            min(p[v] for p in points),
                            max(p[v] for p in points),
                        ),
                    )
                )

    seen: set[tuple[str, str, int, float]] = set()
    findings: list[dict[str, Any]] = []
    for (axis, offset), entries in sorted(planes.items()):
        for index, (group_a, box_a) in enumerate(entries):
            for group_b, box_b in entries[index + 1 :]:
                if group_a == group_b:
                    continue
                if (
                    min(box_a[1], box_b[1]) - max(box_a[0], box_b[0]) > 1e-6
                    and min(box_a[3], box_b[3]) - max(box_a[2], box_b[2]) > 1e-6
                ):
                    key = (*sorted((group_a, group_b)), axis, offset)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(
                        {
                            "parts": sorted((group_a, group_b)),
                            "axis": "xyz"[axis],
                            "offset": round(offset, 6),
                        }
                    )
    return findings


def command_apply_facing(args: argparse.Namespace) -> int:
    """Rewrite angle_degrees from each object's declared facing.

    ``facing`` is the intent and ``angle_degrees`` the derived number. Keeping
    both, with validation that they agree, means a mirrored pair cannot quietly
    end up sharing one angle -- the mistake that turns half a room around.
    """
    document = load_document(args.input)
    changed: list[dict[str, Any]] = []
    for obj in document.get("objects") or []:
        if not isinstance(obj, dict) or "facing" not in obj:
            continue
        centre = obj.get("center")
        if not isinstance(centre, dict) or not _is_number(centre.get("x_cm")):
            continue
        angle = angle_for_facing(_point_tuple(centre), obj["facing"], obj.get("shape", ""))
        if angle is None:
            continue
        angle = round(angle, 3)
        if abs((float(obj.get("angle_degrees", 0)) - angle + 180) % 360 - 180) > 1e-6:
            changed.append(
                {"id": obj.get("id"), "from": obj.get("angle_degrees"), "to": angle}
            )
            obj["angle_degrees"] = angle
    if changed and not args.dry_run:
        _write_json(args.input, document)
    print(json.dumps({"changed": changed, "dry_run": bool(args.dry_run)}, indent=2))
    return 0


def command_check_models(args: argparse.Namespace) -> int:
    """Audit the shape catalogue for the geometry faults that render badly."""
    shapes = args.shapes or sorted(BUILTIN_SHAPES)
    report: dict[str, Any] = {"shapes": {}, "total_overlaps": 0}
    for shape in shapes:
        table = SHAPE_PARAMETERS.get(shape)
        params = {name: bounds[2] for name, bounds in table.items()} if table else {}
        findings = coplanar_overlaps(shape, params)
        if findings:
            report["shapes"][shape] = findings
            report["total_overlaps"] += len(findings)
    report["clean"] = report["total_overlaps"] == 0
    print(json.dumps(report, indent=2))
    return 0 if report["clean"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate canonical floorplan JSON and create Sweet Home 3D projects."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate JSON only.")
    validate_parser.add_argument("input", type=Path)
    validate_parser.add_argument("--report", type=Path)
    validate_parser.set_defaults(function=command_validate)

    build_parser_command = subparsers.add_parser(
        "build", help="Validate JSON and generate SH3D plus optional previews."
    )
    build_parser_command.add_argument("input", type=Path)
    build_parser_command.add_argument("--output", type=Path, required=True)
    build_parser_command.add_argument("--svg", type=Path)
    build_parser_command.add_argument("--png", type=Path)
    build_parser_command.add_argument("--report", type=Path)
    build_parser_command.set_defaults(function=command_build)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a generated SH3D archive.")
    inspect_parser.add_argument("input", type=Path)
    inspect_parser.set_defaults(function=command_inspect)

    fit_parser = subparsers.add_parser(
        "camera-fit",
        help="Solve camera position and lens so a wall fills a share of the frame.",
    )
    fit_parser.add_argument("input", type=Path)
    fit_parser.add_argument(
        "--wall", required=True, help="ID of the wall to frame, e.g. north-wall."
    )
    fit_parser.add_argument(
        "--fill",
        type=float,
        default=0.65,
        help="Fraction of frame width the wall should span (default 0.65).",
    )
    fit_parser.add_argument(
        "--eye", type=float, default=165.0, help="Eye height in cm (default 165)."
    )
    fit_parser.add_argument(
        "--field-of-view",
        type=float,
        default=70.0,
        help="Preferred horizontal field of view in degrees (default 70).",
    )
    fit_parser.add_argument(
        "--apply", help="Camera ID to overwrite in place with the solved values."
    )
    fit_parser.set_defaults(function=command_camera_fit)

    check_parser = subparsers.add_parser(
        "check-models",
        help="Report parts that share a plane and will z-fight into stripes.",
    )
    check_parser.add_argument(
        "shapes", nargs="*", help="Shapes to audit (default: the whole catalogue)."
    )
    check_parser.set_defaults(function=command_check_models)

    facing_parser = subparsers.add_parser(
        "apply-facing",
        help="Set angle_degrees from each object's declared facing.",
    )
    facing_parser.add_argument("input", type=Path)
    facing_parser.add_argument("--dry-run", action="store_true")
    facing_parser.set_defaults(function=command_apply_facing)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
