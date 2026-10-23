#!/usr/bin/env python3
"""Measurement tools for comparing a render against its reference photograph.

Both images share a camera and a frame size, so pixel spans are directly
comparable and every finding can be stated as a ratio. Positions are reported
as fractions of the frame (0 to 1) rather than pixels, so a report stays valid
if either image is resized.

    sheet    side-by-side with a labelled grid, for reading positions by eye
    profile  where colour changes along given rows and columns, in both images
    span     the runs of near-constant colour along one scan line
    region   colour statistics and extreme-value bounding boxes inside a box
    tone     exposure comparison: luminance percentiles, blown and crushed area

Run `sheet` first and look at it. The grid turns "the window is too far left"
into "its left jamb is at 0.24 in the reference and 0.31 in the render", which
is what an edit needs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - dependency is documented in SKILL.md
    raise SystemExit("compare_render.py needs Pillow: python -m pip install Pillow")


def _load(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def _luma(pixel: tuple[int, int, int]) -> float:
    return 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]


def _resolve(value: float, extent: int) -> int:
    """Accept either a fraction of the axis or an absolute pixel index."""
    index = int(round(value * extent)) if 0.0 <= value <= 1.0 else int(round(value))
    return max(0, min(extent - 1, index))


def _line(image: Image.Image, axis: str, at: int) -> list[tuple[int, int, int]]:
    width, height = image.size
    if axis == "row":
        return [image.getpixel((x, at)) for x in range(width)]
    return [image.getpixel((at, y)) for y in range(height)]


def _runs(
    pixels: list[tuple[int, int, int]], threshold: float, minimum: int
) -> list[dict[str, Any]]:
    """Split a scan line into stretches of near-constant colour."""
    runs: list[dict[str, Any]] = []
    start = 0
    anchor = pixels[0]
    for index in range(1, len(pixels) + 1):
        ended = index == len(pixels)
        if not ended:
            current = pixels[index]
            if max(abs(a - b) for a, b in zip(current, anchor)) <= threshold:
                continue
        length = index - start
        if length >= minimum:
            block = pixels[start:index]
            mean = tuple(round(sum(c[k] for c in block) / length) for k in range(3))
            runs.append(
                {
                    "from": start,
                    "to": index - 1,
                    "length": length,
                    "colour": "#%02X%02X%02X" % mean,
                }
            )
        if not ended:
            start = index
            anchor = pixels[index]
    return runs


def _fractions(runs: list[dict[str, Any]], extent: int) -> list[dict[str, Any]]:
    for run in runs:
        run["from_f"] = round(run["from"] / extent, 4)
        run["to_f"] = round(run["to"] / extent, 4)
    return runs


def command_sheet(args: argparse.Namespace) -> int:
    reference = _load(args.reference)
    render = _load(args.render)
    size = args.size
    reference = reference.resize((size, size), Image.LANCZOS)
    render = render.resize((size, size), Image.LANCZOS)

    gap, bar, pad, step = 18, 40, 18, size // 10
    sheet = Image.new(
        "RGB", (size * 2 + gap + pad * 2, size + bar + pad * 2), (16, 20, 24)
    )
    sheet.paste(reference, (pad, pad + bar))
    sheet.paste(render, (pad + size + gap, pad + bar))
    draw = ImageDraw.Draw(sheet, "RGBA")
    try:
        font = ImageFont.truetype("segoeui.ttf", 18)
        small = ImageFont.truetype("segoeui.ttf", 13)
    except OSError:
        font = small = ImageFont.load_default()
    draw.text((pad, pad + 10), "REFERENCE", font=font, fill=(150, 165, 175))
    draw.text((pad + size + gap, pad + 10), "RENDER", font=font, fill=(110, 185, 210))

    # Tenth-of-frame grid on both panels, labelled down the left and along the
    # top, so a position can be read off directly as a fraction.
    for panel in (pad, pad + size + gap):
        for index in range(1, 10):
            offset = index * step
            ink = (255, 255, 255, 110) if index == 5 else (255, 255, 255, 55)
            draw.line(
                [(panel + offset, pad + bar), (panel + offset, pad + bar + size)],
                fill=ink,
            )
            draw.line(
                [(panel, pad + bar + offset), (panel + size, pad + bar + offset)],
                fill=ink,
            )
            label = f".{index}"
            draw.text((panel + offset + 3, pad + bar + 2), label, font=small, fill=(255, 255, 255, 160))
            draw.text((panel + 3, pad + bar + offset + 2), label, font=small, fill=(255, 255, 255, 160))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, "PNG")
    print(json.dumps({"sheet": str(args.output), "panel_size": size, "grid": 0.1}, indent=2))
    return 0


def command_profile(args: argparse.Namespace) -> int:
    reference = _load(args.reference)
    render = _load(args.render)
    report: dict[str, Any] = {"rows": {}, "columns": {}}
    for axis, key, values in (
        ("row", "rows", args.rows),
        ("column", "columns", args.cols),
    ):
        for value in values:
            entry = {}
            for name, image in (("reference", reference), ("render", render)):
                width, height = image.size
                extent = height if axis == "row" else width
                index = _resolve(value, extent)
                pixels = _line(image, "row" if axis == "row" else "column", index)
                runs = _fractions(
                    _runs(pixels, args.threshold, args.min_run), len(pixels)
                )
                entry[name] = {"at_px": index, "runs": runs}
            report[key][f"{value:g}"] = entry
    print(json.dumps(report, indent=2))
    return 0


def command_span(args: argparse.Namespace) -> int:
    image = _load(args.image)
    width, height = image.size
    extent = height if args.axis == "row" else width
    index = _resolve(args.at, extent)
    pixels = _line(image, args.axis, index)
    runs = _fractions(_runs(pixels, args.threshold, args.min_run), len(pixels))
    print(json.dumps({"axis": args.axis, "at_px": index, "runs": runs}, indent=2))
    return 0


def command_region(args: argparse.Namespace) -> int:
    image = _load(args.image)
    width, height = image.size
    x0, y0, x1, y1 = args.box
    left, right = sorted((_resolve(x0, width), _resolve(x1, width)))
    top, bottom = sorted((_resolve(y0, height), _resolve(y1, height)))
    crop = image.crop((left, top, right + 1, bottom + 1))
    pixels = [
        crop.getpixel((x, y)) for y in range(crop.height) for x in range(crop.width)
    ]
    count = len(pixels)
    mean = tuple(round(sum(p[k] for p in pixels) / count) for k in range(3))
    lumas = sorted(_luma(p) for p in pixels)

    # Bounding box of everything materially darker, and everything materially
    # lighter, than the region's own mean: finds a dark object on a pale wall
    # without needing a colour named up front.
    mean_luma = sum(lumas) / count
    dark = [0, 0, -1, -1]
    light = [0, 0, -1, -1]
    for target, box in ((mean_luma - args.contrast, dark), (mean_luma + args.contrast, light)):
        xs: list[int] = []
        ys: list[int] = []
        for y in range(crop.height):
            for x in range(crop.width):
                value = _luma(crop.getpixel((x, y)))
                if (target < mean_luma and value <= target) or (
                    target > mean_luma and value >= target
                ):
                    xs.append(x)
                    ys.append(y)
        if xs:
            box[:] = [min(xs) + left, min(ys) + top, max(xs) + left, max(ys) + top]

    def as_fractions(box: list[int]) -> dict[str, Any] | None:
        if box[2] < 0:
            return None
        return {
            "px": box,
            "left_f": round(box[0] / width, 4),
            "top_f": round(box[1] / height, 4),
            "right_f": round(box[2] / width, 4),
            "bottom_f": round(box[3] / height, 4),
            "width_px": box[2] - box[0] + 1,
            "height_px": box[3] - box[1] + 1,
        }

    print(
        json.dumps(
            {
                "box_px": [left, top, right, bottom],
                "mean_colour": "#%02X%02X%02X" % mean,
                "mean_luma": round(mean_luma, 1),
                "luma_p05": round(lumas[int(count * 0.05)], 1),
                "luma_p95": round(lumas[int(count * 0.95)], 1),
                "darker_than_mean": as_fractions(dark),
                "lighter_than_mean": as_fractions(light),
            },
            indent=2,
        )
    )
    return 0


def command_tone(args: argparse.Namespace) -> int:
    report = {}
    for name, path in (("reference", args.reference), ("render", args.render)):
        image = _load(path)
        width, height = image.size
        lumas = sorted(
            _luma(image.getpixel((x, y)))
            for y in range(height)
            for x in range(width)
        )
        count = len(lumas)
        report[name] = {
            "mean": round(sum(lumas) / count, 1),
            "p05": round(lumas[int(count * 0.05)], 1),
            "p50": round(lumas[count // 2], 1),
            "p95": round(lumas[int(count * 0.95)], 1),
            "blown_pct": round(100 * sum(1 for v in lumas if v > 250) / count, 2),
            "crushed_pct": round(100 * sum(1 for v in lumas if v < 15) / count, 2),
        }
    report["mean_delta"] = round(report["render"]["mean"] - report["reference"]["mean"], 1)
    report["note"] = (
        "A render more than about 20 levels off the reference's mean is an "
        "exposure fault. Fix environment.light_color before editing materials."
    )
    print(json.dumps(report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    sheet = subparsers.add_parser(
        "sheet", help="Side-by-side with a tenth-of-frame grid. Start here."
    )
    sheet.add_argument("reference", type=Path)
    sheet.add_argument("render", type=Path)
    sheet.add_argument("--output", type=Path, required=True)
    sheet.add_argument("--size", type=int, default=620)
    sheet.set_defaults(function=command_sheet)

    profile = subparsers.add_parser(
        "profile", help="Colour transitions along rows and columns of both images."
    )
    profile.add_argument("reference", type=Path)
    profile.add_argument("render", type=Path)
    profile.add_argument(
        "--rows",
        type=lambda s: [float(v) for v in s.split(",")],
        default=[0.25, 0.5, 0.75],
        help="Fractions of frame height (or pixel indices above 1).",
    )
    profile.add_argument(
        "--cols",
        type=lambda s: [float(v) for v in s.split(",")],
        default=[0.25, 0.5, 0.75],
    )
    profile.add_argument("--threshold", type=float, default=24)
    profile.add_argument("--min-run", type=int, default=6)
    profile.set_defaults(function=command_profile)

    span = subparsers.add_parser("span", help="Runs of constant colour on one scan line.")
    span.add_argument("image", type=Path)
    span.add_argument("--axis", choices=("row", "column"), default="row")
    span.add_argument("--at", type=float, required=True)
    span.add_argument("--threshold", type=float, default=24)
    span.add_argument("--min-run", type=int, default=6)
    span.set_defaults(function=command_span)

    region = subparsers.add_parser(
        "region", help="Colour statistics and light/dark extents inside a box."
    )
    region.add_argument("image", type=Path)
    region.add_argument(
        "--box",
        type=lambda s: [float(v) for v in s.split(",")],
        required=True,
        help="x0,y0,x1,y1 as fractions of the frame.",
    )
    region.add_argument("--contrast", type=float, default=35)
    region.set_defaults(function=command_region)

    tone = subparsers.add_parser("tone", help="Exposure comparison of the two images.")
    tone.add_argument("reference", type=Path)
    tone.add_argument("render", type=Path)
    tone.set_defaults(function=command_tone)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
