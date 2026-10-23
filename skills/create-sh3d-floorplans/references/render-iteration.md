# Render-and-compare iteration

Use this loop when a source image should be approximated from the same point of
view. The purpose is to test geometry and framing, not to certify dimensions.

## Viewer setup

The official open-source Sweet Home 3D JS Viewer is distributed separately
under the GPL:

`https://sourceforge.net/projects/sweethome3d/files/SweetHome3D-viewer/`

Do not bundle the viewer in this skill. Extract it locally, then prepare a test
scene:

```powershell
python scripts/prepare_js_viewer.py `
  --viewer-dir C:\path\to\SweetHome3DJSViewer `
  --sh3d plan.sh3d `
  --output-dir .tmp\plan-render `
  --camera "Source photo match"
```

Serve the output over HTTP rather than opening `index.html` with a `file:` URL:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory .tmp\plan-render
```

Open `http://127.0.0.1:8765/`. The page sets
`document.body.dataset.status="ready"` and `window.renderReady=true` after model
loading, which makes screenshot automation deterministic.

The WebGL viewer is useful for fast silhouette and camera checks. It is not the
desktop application's photorealistic photo renderer; do the final material,
shadow, sunlight, and light-intensity check with **3D view > Create photo** in
Sweet Home 3D.

## Camera model

Sweet Home 3D uses perspective projection in observer/virtual-visit mode.
Canonical JSON stores an intuitive position and target; the exporter derives
yaw and pitch.

- yaw 0 faces plan-south; yaw π faces plan-north;
- `pinhole` has infinite depth of field and is best for layout comparison;
- `normal` adds depth of field;
- `fisheye` intentionally bends the view;
- `spherical` is for panoramic output.

A horizontal field of view near 63 degrees is a useful neutral starting point.
For a 36 mm-wide full-frame sensor this is roughly a 29 mm lens. A wider field
of view fits more foreground and ceiling but exaggerates near objects and
corner recession. Moving the camera backward is not equivalent to widening the
lens.

## Comparison order

1. Match horizon/eye height and the central vanishing direction.
2. Match large architectural silhouettes: wall limits, windows, galleries,
   ceiling, and fixed cabinetry.
3. Match foreground cropping and occlusion by changing camera distance and
   field of view.
4. Correct object orientation and footprint.
5. Refine conspicuous curves and openings.
6. Tune colors, light power, and materials last.

Keep each revision in canonical JSON. Rebuild the SH3D, reload the viewer, and
capture the same named camera. Avoid compensating for a wrong lens by changing
room dimensions.

## Interpreting generated reference images

AI-generated interiors may look photographic while violating a single physical
camera model: opposite sides can imply different vanishing points, repeated
objects can change scale inconsistently, and reflections may invent geometry.
If one camera cannot align every cue, prioritize stable architecture and
symmetry, document the mismatch, and treat the render as a plausible
reconstruction rather than an exact inverse.

## The stale-first-frame trap (read this before debugging a render)

The viewer draws its first frame as soon as `Home.xml` is parsed -- while
furniture models are still being fetched from the archive. Pieces whose model
has not arrived yet are drawn as **untinted white placeholder boxes**. Because
the preparation script uses `roundsPerMinute: 0` there is no animation loop, so
nothing ever redraws and a screenshot captures that stale frame forever.

The symptom is distinctive and extremely misleading: rooms, walls and ceilings
show their correct colours (they are built synchronously) while **every piece of
furniture is white and box-shaped**. It looks exactly like a colour or material
bug in the generated SH3D, and it is not -- the file is fine.

The generated page therefore calls `canvas3D.drawScene()` once loading
completes, before setting `renderReady`, and exposes `window.forceRedraw()` so a
capture script can force a fresh frame immediately before reading the canvas.
Always call it right before `toDataURL()`:

```js
window.forceRedraw();
await new Promise(r => setTimeout(r, 400));
const png = document.getElementById("viewerCanvas").toDataURL("image/png");
```

Do not conclude anything about colour, material or geometry from a frame that
was not preceded by a forced redraw. Diagnosing this the hard way costs hours:
model loading, MTL files, vertex normals, face winding, furniture attributes and
the piece `color` value all check out clean, because none of them is the cause.

## Capture pitfalls

Three further harness bugs produce convincingly wrong conclusions:

* **Aspect ratio.** The viewer canvas is stretched by CSS to the viewport, so
  renders inherit the browser window's shape. `prepare_js_viewer.py` pins the
  canvas to its exact drawing-buffer size; compare against a source image at
  the same aspect ratio.
* **Drawing buffer.** `canvas.toDataURL()` on a WebGL canvas returns a torn or
  cleared frame unless the context was created with `preserveDrawingBuffer`.
  The generated page forces this before the viewer loads.
* **Stale scenes.** The browser may serve a cached `scene.sh3d` across rebuilds
  even with `Cache-Control: no-store`, so real edits look like no-ops and
  renders come back byte-identical. Give each build a content-addressed file
  name (`scene-<hash>.sh3d`) rather than relying on cache headers.

When a render looks unchanged after an edit, verify the scene actually reached
the page before theorising about geometry: check the fetched file name and
compare its byte length against the file on disk. And calibrate any pixel
metric before trusting it -- a "saturated pixel" threshold that excludes
`#51372C` brown will report a fully coloured render as empty.

## Desktop Sweet Home 3D is stricter than the JS viewer

The JS viewer resolves each model by its exact ZIP entry name and shows back
faces, so it renders files the desktop application draws completely wrong. Three
defects were invisible in the browser preview.

### Every model needs its own directory (the big one)

Sweet Home 3D resolves a model's companion files through its **containing
directory**. Put every OBJ in a shared `models/` folder and the application
treats them as one bundle: every piece of furniture is drawn with the same mesh,
whichever OBJ it finds first. A plan whose first model happened to be a window
frame rendered every sofa, chair and table as a window muntin grid, which reads
convincingly as "all the furniture became bookshelves".

Sweet Home 3D's own files show the required layout — one numbered directory per
model:

```
1/bed/bed.obj
3/bed140x190/bed140x190.obj
5/bed90x190/bed90x190.obj
```

`model_registry()` reproduces that layout and a test asserts no two models share
a directory. When furniture looks identical or wrong in the desktop application
but correct in the browser, check this first.

### Duplicate faces z-fight

Emitting each face in both windings to force two-sided visibility puts two
coplanar polygons at identical depth. The desktop renderer flickers between them
per pixel, banding every surface with stripes so smooth furniture looks slatted.
Two-sided visibility comes from `backFaceShown="true"`. A test enforces one face
per surface, including inside composite models, where overlapping solid parts
produce the same artefact.

### Face orientation: consistency first, then the right convention

An inward-wound face is culled, so you see through that surface into the solid.
Getting this right needs two separate things, and fixing only one produces a
confusingly partial result.

**1. Every face must agree with its neighbours.** The revolved generators
(cylinder, rounded-rectangle and annular prisms) wind opposite to the cuboid
ones, and *within* a revolved group the caps disagree with the walls. No
whole-group flip repairs that -- the symptom is a solid missing individual
surfaces, such as a banquette with no seat or an island with no top.
`_finalize_obj` propagates orientation breadth-first across shared edges
(neighbouring faces are consistent when they traverse a shared edge in opposite
directions), then orients each group by signed volume. Signed volume is used
rather than a centroid test because it stays correct for non-convex solids such
as the C-shaped banquette, whose inner wall legitimately faces the centroid.

**2. Sweet Home 3D wants the reverse of the right-hand convention.** Once every
group is consistently outward-wound by the right-hand rule, the whole thing is
reversed before writing. Emitting right-hand outward renders *every* solid
inside out in the application. `FLIP_WINDING` therefore defaults on; set
`FLOORPLAN_FLIP_WINDING=0` to emit the right-hand convention for comparison.

The order these were discovered matters, because each partial fix looked like
evidence for the wrong conclusion:

| Consistency pass | Winding | Result in the application |
| --- | --- | --- |
| no | reversed | cuboids solid, curved shapes broken |
| yes | right-hand outward | everything inside out |
| yes | reversed | correct |

Tests assert that every group encloses real volume and that all models agree on
a single winding direction. Mixed orientation is the actual failure mode: it
renders some furniture solid and the rest inside out, which is far harder to
read than a uniform error.

### What is *not* required

Sweet Home 3D's own model files contain quads and n-gons of up to sixteen
vertices, carry no vertex normals, and ship without any `.mtl` file, yet load
and tint correctly. Quads, missing normals and absent material libraries are
therefore **not** causes of bad rendering — do not spend time there. The
exporter still triangulates and writes explicit normals because it removes
ambiguity cheaply, but that was never the defect.

### Compare against a real file

The fastest way to diagnose a format problem is to save a small home from Sweet
Home 3D itself and diff its `Home.xml` and archive layout against a generated
one. That single comparison identified the directory rule after several wrong
hypotheses derived from browser renders alone.

Because these defects are invisible in the browser preview, **a plan is not
finished until it has been opened once in the desktop application.**
