---
name: create-sh3d-floorplans
description: Create, validate, inspect, and revise Sweet Home 3D floorplan projects from room descriptions, measurements, sketches, or reference images. Use when producing canonical floorplan JSON, inferring a cautious layout from incomplete visual evidence, generating editable .sh3d files, modelling levels/floors/balconies/walls/doors/windows/ceiling heights/furniture, creating 2D previews, or diagnosing a generated SH3D archive. Do not use for construction-ready, permit, structural, code-compliance, or survey claims.
---

# Create SH3D Floorplans

Produce an auditable JSON source, validate it deterministically, and compile it
into an XML-based SH3D archive for Sweet Home 3D 5.3 or newer.

**The JSON is the source of truth.** Never hand-edit `Home.xml` or the OBJ files
inside a generated archive. Fix the JSON, or fix `scripts/floorplan_tool.py`,
then regenerate.

## Start from the template

Copy `references/templates/single-room.json` rather than writing a document from
scratch. It is a complete, validating one-room plan with every required field
present, and the test suite keeps it valid. Replace the values, keep the shape.

Then read `references/schema-guide.md` for field meanings and the parameterised
shapes, and `references/inference-workflow.md` when the source is an image.

**When a photograph is the source, read
`references/matching-a-reference-photo.md` before writing any dimension.** It
gives the method for getting real numbers out of an image instead of guessing:
find the scale from a feature of known height, express everything else as a
fraction of the wall, and solve the camera from where the floor and ceiling
lines fall. Guessed dimensions produce a room that is recognisably *a* room and
recognisably not *the* room.

## Build in layers, verifying each one

This is the order for the *first* build; the tier list in the next section is
the order for *revising* one against a reference. They agree, because both put
the room and the viewpoint before anything standing in it.

Do not author the whole document and then debug it. Each layer below is cheap to
check and expensive to fix once other things depend on it.

1. **Shell.** Level, four walls, one space polygon. Validate. Look at the 2D PNG.
2. **Camera.** Solve it, render, confirm the framing before any furniture
   exists. A wrong camera makes correct geometry look broken.
3. **Exposure.** Set `environment.light_color` and check a render before
   judging any material colour. Everything looks wrong under the wrong key.
4. **Fixed architecture.** Openings, cabinetry runs, ceiling, galleries.
5. **Furniture and lights.**
6. **Detail and colour** last.

## Know what to fix next, and when to stop

**Read `references/review-loop.md` before the first revision.** It is the
priority order for matching a reference, and the rule for stopping.

The short form:

| Tier | | Budget |
| --- | --- | --- |
| 0 | Validates and opens | must pass |
| 1 | **Overall resemblance** — would a person describe both images the same way? | diagnosis |
| 2 | Envelope and camera | 3 cycles |
| 3 | Fixed architecture: openings, then structure, then built-ins | 3 cycles |
| 4 | Major furniture | 2 cycles |
| 5 | Colour and light | 1 cycle |
| 6 | Detail | optional |

**Do not spend a cycle on tier N+1 while tier N is failing.** Detail applied
over a wrong camera has to be redone once the camera moves.

Within furniture, the order is presence, then class, then **direction**, then
position and size, then height, and only last the actual shape. A box in the
right place facing the right way beats a well-modelled piece facing the wrong
way. Primary pieces — the ones that define the room's purpose or fill much of
the frame — outrank every problem with a secondary piece, and nothing tertiary
gets added while a primary one is wrong.

Stop when tiers 0–4 pass and a full cycle promotes nothing, when a tier's
budget is spent, or at ten render cycles whatever the state. **Never iterate on
what the exporter cannot express** — patterns, textures, veining, wood grain —
because each attempt looks different without getting closer. Name those once
and move on.

## Get an independent read

Judging your own reconstruction is unreliable: you know what each shape was
meant to be, so you see the intent rather than the image. Spawn a subagent with
no context from the build and give it the prompt in
`references/reviewer-prompt.md`. Run it at the end of tier 2 and again at the
end of tier 4.

Tell it about `scripts/compare_render.py`, which does the measuring:

```powershell
python scripts/compare_render.py sheet reference.png render.png --output grid.png
python scripts/compare_render.py tone reference.png render.png
python scripts/compare_render.py profile reference.png render.png --rows 0.3,0.6 --cols 0.25,0.75
```

`sheet` is the one to run first — a tenth-of-frame grid over both images turns
"the window is too far left" into "its jamb is at 0.24 in the reference and
0.31 in the render", which is what an edit needs. Without being pointed at the
tool a reviewer writes its own pixel-measuring code and measures worse.

## Commands

```powershell
python scripts/floorplan_tool.py validate input.json --report validation.json
```

```powershell
python scripts/floorplan_tool.py build input.json --output plan.sh3d --svg plan.svg --png plan.png --report validation.json
```

```powershell
python scripts/floorplan_tool.py inspect plan.sh3d
```

Solve a camera so a named wall fills a given share of the frame, instead of
guessing position and lens by hand:

```powershell
python scripts/floorplan_tool.py camera-fit input.json --wall north-wall --fill 0.7 --eye 165 --apply source-match
```

`--apply` writes the solved values into that camera. Omit it to preview the
numbers. If the report says `clamped_to_plan: true`, the camera hit the far wall
and the lens was widened to compensate — that almost always means the room is
modelled too shallow, not that the lens is too narrow.

`camera-fit` only controls how much of the frame a wall spans. When the
vertical framing matters too — how much ceiling and floor are in shot — solve
it from the source instead; `references/matching-a-reference-photo.md` has the
formula.

Audit the shape catalogue for the geometry faults that render badly:

```powershell
python scripts/floorplan_tool.py check-models
```

It reports parts of a model that share a plane and overlap inside it. Those
z-fight and band the surface with stripes. Run it after touching any generator.

## Pre-flight checklist

Run through this before declaring a plan finished. Validation catches the first
four automatically; the rest need judgement.

- [ ] `validate` reports zero errors **and** zero warnings.
- [ ] Exposure checked with `compare_render.py tone`, not by eye.
- [ ] An independent reviewer has compared render against reference.
- [ ] Every primary piece of furniture faces the right way.
- [ ] The tier reached is stated, with the residual gaps named.
- [ ] Every observer camera stands inside a floor-enabled space polygon.
- [ ] No object sits within 90 cm of a camera unless it is meant to be cropped.
- [ ] Every room either has `ceiling.enabled` or an overhead shape covering it.
- [ ] Space polygons still match the walls after any resize.
- [ ] The stored source camera is named and `matches_source_id` is set.
- [ ] Lamps that matter are `category: "light"` with `light_power`.
- [ ] Every geometric entity carries honest `evidence`; unseen walls stay
      low-confidence rather than being quietly promoted to observed.

## Check which way everything faces

Orientation is the error most likely to survive review: a backwards sofa
validates, renders, and still looks like furniture. Half a room can be turned
around without anything looking obviously broken.

`angle_degrees` rotates the piece; its front is the model's depth axis, which
Sweet Home 3D points **south at angle 0** — then 90 west, 180 north, 270 east.
Guessing the opposite is easy and costly.

- Put `facing` on every piece that has a front: `"north"`, `"east"`, `"south"`,
  `"west"`, or a point to face. Validation errors when `angle_degrees`
  disagrees and tells you the right number.
- `python scripts/floorplan_tool.py apply-facing input.json` derives every
  angle from its declared facing.
- `facing.into-wall` warns when a front points outside the room.
- The 2D plan PNG draws a facing arrow on every directional piece — check it
  before rendering anything in 3D.

**Verify an orientation convention on a piece with an unmistakable front.** A
bookcase against a wall is ideal: right angle, shelves face the room; wrong
angle, the room sees a flat back panel. A sofa is a poor test — from an
elevated camera its tall back and arms swap apparent order, so both
orientations look plausible and a whole project can be built on the wrong
reading.

## Traps that cost real debugging time

**Resizing a room means editing two places.** Wall endpoints and the space
polygon are independent. Move the walls only and the floor stays behind: the
room renders with a hole, and a camera standing in it trips
`camera.outside-space`. The polygon is inset half a wall thickness from the wall
centrelines.

**A camera outside the walls renders the building cut open.** Sweet Home 3D
switches to a dollhouse view, which reads as catastrophic geometry failure but
is purely a camera error. Check the camera before suspecting the model.

**A room with no ceiling is open to the sky.** Disabling `ceiling.enabled` on a
double-height space and forgetting an overhead shape puts background above the
walls in every interior view.

**Widening the lens is not the same as stepping back.** Backing up changes
occlusion and spatial relationships; widening only crops in more while
exaggerating near objects. If the foreground looms, move the camera or deepen
the room. Never fix a framing problem by distorting room dimensions.

**Every model needs its own directory in the archive.** Sweet Home 3D resolves
a model's companion files through its containing directory, so models sharing
one folder collapse into a single bundle and every piece of furniture is drawn
with the same mesh. The exporter writes `1/sofa/sofa.obj`, `2/chair/chair.obj`
and so on, matching the layout Sweet Home 3D writes itself. If furniture looks
identical or wrong in the desktop application but correct in the browser
preview, check this first.

**Verify in the desktop application before calling a plan finished.** The JS
viewer resolves exact entry names and shows back faces, so it renders files that
Sweet Home 3D draws completely wrong. When a format question comes up, save a
small home from Sweet Home 3D and diff it against a generated one rather than
theorising from browser renders.

**Faces must be consistently wound, and the generators disagree.** The revolved
generators wind opposite to the cuboid ones, and within a revolved group the
caps disagree with the walls. An inward-wound face is culled, so you see through
that surface into the solid — a banquette loses its seat, an island loses its
top. `_finalize_obj` repairs this automatically by propagating orientation
across shared edges and then orienting each group by signed volume, so new
generators do not have to get winding right. A test enforces it.

**Never let two parts share a plane.** Coplanar faces both get drawn, because
the exporter sets `backFaceShown`, and they z-fight into stripes — smooth
furniture ends up looking slatted or shelved. Merely stacking one part on
another is enough; parts must overlap slightly instead of butting together.
`check-models` finds these, and a test keeps the solid furniture clean.

**Never emit a face twice.** Two coplanar polygons at the same depth z-fight and
band every surface with stripes in Sweet Home 3D's desktop renderer — smooth
furniture ends up looking slatted or shelved. Two-sided visibility comes from
`backFaceShown="true"` in the XML, never from a reversed duplicate winding. The
same applies inside composite models: two solid parts that overlap share
coplanar faces, so offset or inset them rather than letting them intersect
flush. A test enforces this for every shipped model.

**Procedural models have fixed orientations.** OBJ geometry is Y-up
(X = width, Y = height, Z = depth). The curved banquette's opening faces +X, so
`angle_degrees` rotates the opening *away* from you if you assume otherwise.
Confirm against a render rather than reasoning about it.

**The JS viewer's first frame is stale.** See `references/render-iteration.md`.
Always call `window.forceRedraw()` before capturing, or the screenshot shows
untinted placeholder boxes drawn before the models finished loading.

**Calibrate a pixel metric before trusting it.** A "saturated pixel" threshold
that happens to exclude mid-tone browns will report a fully coloured render as
empty.

**A washed-out render is an exposure fault, not a colour fault.** Sweet Home 3D
scales the whole view by `environment.light_color`; the default `#D0D0D0` is
what the application itself writes. Anything brighter pushes mid-tone surfaces
to white and reads as wrong materials. Rooms differ: a black-and-marble kitchen
wanted `#FAFAFA` while a dark panelled living room wanted `#BCB6AC`. Sample
pixels before editing a single material.

**Floors and furniture are not lit alike.** A room floor renders at roughly its
own colour; a piece standing on it renders at about 0.8 of its colour. Two
surfaces that differ by less than that gap are indistinguishable, which is how
a rug ends up invisible against a floor it was meant to contrast with.

**Detailing stretches with the piece.** An OBJ is written in a unit cube and
then scaled independently on each axis, so a muntin at a fixed fraction of the
model becomes 14 cm thick on a 124 × 590 cm window. `window` and `wall-panel`
pre-compensate from the piece's proportions; a new generator with fine detail
needs the same treatment. See `ASPECT_AWARE_SHAPES`.

**Detail that sits on another model must match that model's geometry.** The
bookcase places shelves at fixed fractions of its carcass. Book rows put on a
round elevation pitch float between them and read as scattered blocks rather
than packed spines. Read the generator before choosing elevations.

## Non-negotiable rules

- Reject duplicate IDs, missing references, zero-length walls, self-intersecting
  spaces, openings outside walls, overlapping openings, and openings taller than
  their walls.
- Require evidence metadata on every geometric entity.
- Represent partial upper floors and balconies as upper-level spaces with
  enabled floors. Represent their guard edges as low `railing` walls.
- Represent atrium or double-height cutouts as `void` spaces with floor and
  ceiling disabled.
- Use explicit wall-height overrides for double-height walls.
- Store at least one `observer` camera when a perspective image is a source.
  Match framing with position, target, and field of view; do not bake camera
  guesses into the room geometry.
- Export visible lamps as `light` objects with explicit power and color when
  illumination matters.
- Prefer a plausible simple plan with low-confidence assumptions over
  unsupported detail.
- State that a single perspective image cannot reveal a complete or accurately
  scaled floorplan.
- Do not describe generated plans as construction documents.

## Output contract

Produce:

- canonical `.json`;
- editable `.sh3d`;
- `.svg` preview, and `.png` when Pillow is available;
- validation `.json`;
- a short assumption summary.

**Then share the results in the conversation:**

- Post the **rendered view next to its reference image** for every room that had
  an image source, so the user can judge the match without opening anything.
  Put them adjacent — a render on its own is not reviewable.
- Give the **path to each `.sh3d`** as a clickable link. Not a description of
  where it is, and not an embedded data-URI download button.
- Name what still does not match, briefly and specifically. Do not present an
  approximate reconstruction as finished.

Render the stored source camera with the official Sweet Home 3D JS Viewer via
`scripts/prepare_js_viewer.py`; read `references/render-iteration.md` first.
Revise the JSON and regenerate — never edit the archive.

Open the result with **File > Open** in Sweet Home 3D. Do not use the furniture
or background-image import commands. Keep the JSON beside the SH3D file.

## Model and colour notes

The exporter embeds closed, Y-up procedural OBJ models with a companion MTL for
doors, windows, openings, bookcases, books, cabinets, counters, seating, tables,
rounded islands, curved banquettes, chandeliers, fireplaces, railings, coffered
ceilings, crown mouldings, wall panels, sconces, rugs, curtains, and sunburst
medallions. Only the models a document references are embedded. These are design
proxies, not product models.

Overall lighting is authored, not fixed. The optional top-level `environment`
block carries `light_color`, `ceiling_light_color`, `sky_color`, `ground_color`
and `walls_alpha`; omitting it uses Sweet Home 3D's own defaults. `light_color`
is the strongest single control over how a render reads.

Furniture colour requires the MTL: Sweet Home 3D tints a piece by overriding its
model's material, so a model that declares no material always renders white.

For readable schematic output, favour light neutral floors and walls, muted
mid-tone furniture, and strong but non-neon opening contrast. Large near-black
surfaces cost geometry legibility. Colours serialise as opaque ARGB hexadecimal.

## Compatibility boundary

The exporter writes `Home.xml` plus embedded model assets into the SH3D ZIP
container and declares the Sweet Home 3D 7.0 format level. It does not write the
legacy Java-serialized `Home` entry, so generated files require Sweet Home 3D
5.3 or newer; 5.3–6.x may warn about the declared version. Sweet Home 3D's
desktop application is free software — a file that opens incorrectly is a bug in
the generated geometry, not a licensing limitation. Read
`references/sh3d-export-contract.md` when changing XML generation.
