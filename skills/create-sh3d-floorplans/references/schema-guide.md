# Canonical floorplan JSON

## Contents

- Coordinate system
- Entity model
- Evidence and uncertainty
- Geometry invariants
- Sweet Home 3D mapping
- Environment (lighting)
- Parameterised shapes

## Coordinate system

The canonical document always uses centimeters:

- origin: top-left of the plan;
- positive X: right/east;
- positive Y: down/south;
- positive Z: up;
- angles: clockwise degrees in JSON and radians in SH3D.

Convert feet, inches, meters, and millimeters before creating canonical JSON.

## Entity model

`levels`
: Floor elevations, slab thicknesses, and normal ceiling heights.

`cameras`
: Aerial or observer viewpoints expressed as a position, target, horizontal
  field of view, and lens. A camera may cite the source image it is intended to
  match. At most one camera is active.

`walls`
: Straight centerline segments. A wall may override the level ceiling height.
  Use `role: railing` for an open procedural guardrail along the segment.

`spaces`
: Closed, simple polygons. Rooms and balconies create optional floor and ceiling surfaces. A `void` normally disables both.

`openings`
: Doors, windows, sliding doors, or open passages placed by center distance from a wall's start point.

`objects`
: Layout-scale furniture, lighting, and architectural placeholders. Supported
  built-in shapes include closed primitives plus bookcases, books, cabinets,
  counters, sofas, chairs, tables, a rounded island, curved banquette,
  chandelier, linear pendant, fireplace surround, and railings.

Light-category objects may add `light_power` from 0 to 1 and a hexadecimal
`light_color`. These values produce a real SH3D `light` element rather than a
decorative furniture-only object.

## Evidence and uncertainty

Every geometric entity requires:

```json
{
  "evidence": {
    "basis": "estimated",
    "confidence": 0.45,
    "note": "Width inferred from a typical eight-seat dining table."
  }
}
```

Use:

- `observed`: directly visible, but not necessarily measured;
- `estimated`: inferred from scale cues or conventions;
- `specified`: explicitly provided by the user or a measured drawing;
- `derived`: computed from other geometry.

`confidence` describes confidence that the value is close to reality, not confidence that the JSON is valid.

## Geometry invariants

The script enforces invariants JSON Schema cannot express:

- IDs are globally unique across geometry entities.
- References resolve to existing entities of the correct kind.
- Levels have unique elevation indices.
- Walls have positive length, thickness, and height.
- Space polygons have nonzero area and do not self-intersect.
- Openings fit completely within their wall and below its top.
- Openings on the same wall do not overlap.
- Object dimensions are positive.
- Camera source references resolve, position and target differ, field of view is
  between 10 and 170 degrees, and at most one camera is active.
- Light power and color are valid and are not silently accepted on non-light
  objects.
- Image-only projects identify their measurements as estimated or mixed.

Warnings identify likely modeling problems that are not always invalid, such as three-way wall junctions or furniture centers outside all spaces.

## Sweet Home 3D mapping

| JSON | SH3D `Home.xml` |
|---|---|
| level | `level` |
| wall | `wall` |
| space | `room` |
| door/window/opening | `doorOrWindow` |
| generic object | `pieceOfFurniture` |
| light object | `light` with nested `lightSource` |
| source-match observer camera | active `observerCamera` plus `storedCamera` |
| balcony/gallery floor | upper-level `room` with `floorVisible=true` |
| railing | open procedural `pieceOfFurniture` placed from the wall segment |
| atrium void | `room` with floor and ceiling hidden |

The SH3D format does not have a native semantic balcony or railing element. The mapping preserves basic geometry and editability.

## Which way a piece faces

`angle_degrees` rotates a piece about its centre. The front is the model's
depth (+Z) axis, and Sweet Home 3D maps it to plan **south** at angle 0:

| angle_degrees | front faces |
| --- | --- |
| 0 | south (+y) |
| 90 | west (−x) |
| 180 | north (−y) |
| 270 | east (+x) |

`front(angle) = (−sin angle, cos angle)`. The `curved-banquette` is the one
exception: its opening is cut around the model's width (+X) axis, so it reads
90 degrees off everything else. The tool accounts for this.

Orientation is the error most likely to survive review. A backwards sofa
validates, renders, and still looks like furniture in plan — nothing flags it
except looking at the right surface. Three defences:

**Declare `facing` rather than trusting the number.** It takes `"north"`,
`"east"`, `"south"`, `"west"`, or a point to face:

```json
"facing": "east"
"facing": { "x_cm": 425, "y_cm": 393 }
```

Validation errors if `angle_degrees` disagrees, and the message gives the
correct angle. This is what stops a mirrored pair from quietly sharing one
number, which is the usual way half a room ends up turned around.

```powershell
python scripts/floorplan_tool.py apply-facing input.json
```

derives every angle from its declared facing.

**A warning fires when a front points at a wall.** Any piece with a
distinguishable front — seating, bookcases, cabinets, counters, fireplaces,
sconces, wall panels — is checked by probing just in front of it; if that point
falls outside the room, `facing.into-wall` reports the angle that would fix it.

**The 2D plan draws a facing arrow** on every such piece, so a backwards item
is visible in the PNG without opening the 3D view at all.

## Environment (lighting)

The optional top-level `environment` object controls the whole 3D view.
Omitted keys fall back to Sweet Home 3D's own defaults, so the block can be
left out entirely.

| Key | Default | Effect |
| --- | --- | --- |
| `light_color` | `#D0D0D0` | Scales the entire view. The strongest single control over how a render reads. |
| `ceiling_light_color` | `#D0D0D0` | Light cast from above. |
| `sky_color` | `#CCE4FC` | Backdrop seen through openings. |
| `ground_color` | `#A8A8A8` | Ground plane outside the walls. |
| `walls_alpha` | `0` | Wall transparency, 0 opaque to 1 invisible. |

Set this **before** judging any material colour. A washed-out render is an
exposure fault: anything brighter than the default pushes mid-tone surfaces to
white and reads as wrong materials. Different rooms want different keys -- a
black-and-marble kitchen took `#FAFAFA`, a dark panelled living room `#BCB6AC`.

Room floors and furniture are not lit alike. A floor renders at roughly its own
colour while a piece standing on it renders at about 0.8 of its colour, so two
surfaces closer than that gap are indistinguishable in the result.

## Parameterised shapes (`shape_params`)

Some shapes generate their geometry from parameters instead of using one fixed
mesh. Supply them under `shape_params`; every distinct parameter set is baked
into its own embedded OBJ, so two objects sharing a shape but not its
parameters get separate models. Values are clamped to the ranges below, and
supplying `shape_params` for a shape that does not accept them is an error.

| Shape / opening type | Parameters (min, max, default) |
| --- | --- |
| `coffered-ceiling` | `columns` (1, 12, 3), `rows` (1, 12, 3), `beam_ratio` (0.02, 0.4, 0.12), `cross_braces` (0, 1, 0) |
| `wall-panel` | `columns` (1, 8, 1), `rows` (1, 8, 1), `border_ratio` (0.02, 0.45, 0.12) |
| `deco-railing` | `bays` (1, 24, 4), `rays` (2, 16, 7) |
| `chandelier` | `tiers` (1, 6, 3), `drop_ratio` (0, 0.9, 0.35) |
| `rug` | `border_ratio` (0.02, 0.45, 0.08) |
| `curtain` | `folds` (2, 24, 6) |
| `sunburst` | `rays` (2, 24, 9) |
| `crown-moulding` | `steps` (1, 6, 3), `band_ratio` (0.005, 0.2, 0.03) |
| `window` (opening type) | `columns` (1, 8, 2), `rows` (1, 16, 2) |

`chandelier`'s `drop_ratio` is the share of the piece's total height taken by
the chain above the body. A fixture's `height_cm` has to span canopy to lowest
drop, because that is the box Sweet Home 3D scales; without a separate chain
length the tiers could only sit directly under the ceiling, so hanging a light
low in a double-height room meant stretching the tiers to match.

`rug` is an underfoot shape, drawn as an outline in the 2D plan and exempt from
the camera clearance rule -- a mat cannot block a view however close it comes.
Anything under 12 cm tall is treated the same way.

`crown-moulding` is hollow and overhead: only the perimeter band is solid, so
it can be dropped over a room without hiding the ceiling behind it.

A bordered floor or a two-tone surface needs **two pieces**, because each
carries a single colour. The kitchen's black inlay is a dark slab with a pale
one inset on top of it.

`deco-railing` is the model used for walls with `role: "railing"`, so railing
walls may also carry `shape_params`. `window` is an *opening* type rather than
an object shape, and controls the muntin grid.

`coffered-ceiling` is an overhead shape: it is drawn as a dashed outline in the
2D plan so it does not hide the level beneath it. Place it on the level whose
elevation plus `elevation_cm` plus `height_cm` reaches the top of the volume --
for a double-height room that is normally the upper level.

Only the models a document actually references are embedded in the SH3D, and
each ships with a companion `.mtl` declaring a neutral material.
