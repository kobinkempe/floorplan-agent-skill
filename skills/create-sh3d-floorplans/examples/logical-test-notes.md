# Logical test of the image-to-floorplan workflow

These samples test whether the representation and compiler can preserve the
architectural facts that are visible in the reference images while keeping
guesses explicit. A single perspective image does not reveal a measured plan,
so dimensions and unseen boundaries remain hypotheses.

## Test sequence

1. Inventory only visible architectural evidence: wall directions, openings,
   apparent floor levels, railings, ceiling type, and large fixed features.
2. Separate visible facts from inferred geometry. Every inferred entity carries
   an evidence record, confidence, and a short rationale.
3. Choose conservative dimensions that make the visible composition possible.
4. Encode structural geometry before furniture:
   levels, spaces and voids, walls, windows and doors, then fixtures.
5. Validate topology, references, opening placement, vertical extents, and the
   provenance rules.
6. Compile the same JSON to both SH3D and a plan preview.
7. Inspect the SH3D ZIP, XML ordering, referenced models, level references, and
   archive CRCs independently of the source JSON.
8. Render a named source-match camera with the official Sweet Home 3D JS Viewer,
   compare the silhouette to the source, and revise camera and geometry.

## Reference 1: library dining room

Visible:

- one enclosed room with a high coffered ceiling;
- full-height perimeter bookcases and a built-in illuminated bar cabinet;
- a long dining table, eight chairs, and a central chandelier;
- no reliable exterior window is visible.

Inferred:

- a 650 x 550 cm rectangular room and a 320 cm ceiling;
- an entry door in the unseen camera-side wall;
- procedural bookcases and book rows, a pedestal table, eight chairs, and a
  source-match camera.

The entry location and all dimensions are deliberately low-confidence. The
sample does not invent windows merely to make the room conventional.

## Reference 2: black marble kitchen

Visible:

- a single-level kitchen with a standard-height ceiling;
- a long rear cabinet/counter run and tall cabinetry on the right;
- a large rounded island, curved banquette, small table, and pendant;
- no reliable window or door placement is visible.

Inferred:

- a 650 x 500 cm room and a 285 cm ceiling;
- an open passage on the unseen camera-side boundary;
- lower and upper cabinetry, backsplash, a rounded-rectangle island, an open
  curved banquette, a six-light linear pendant, and a source-match camera.

The wide passage tests wall-hosted openings without asserting a swinging door.

## Reference 3: double-height living room

Visible:

- a two-story-height central volume;
- two tall front windows flanking a centered fireplace;
- upper galleries or balconies on the left and right with railings;
- a central chandelier descending through the double-height space;
- side doors at the lower level.

Inferred:

- an 850 x 900 cm ground footprint;
- a 330 cm lower story and 370 cm upper portion;
- two 130 cm-wide side galleries beginning behind the window plane;
- a central upper-level void, open post-and-rail guards, a firebox wall cutout,
  modeled seating, and a source-match camera.

This sample exercises the important vertical semantics: the galleries are
upper-level floor spaces, the center is a non-surfaced void, the railing
segments become open models, and the tall windows and firebox remain attached
to the double-height exterior wall.

## Automated results

All three examples:

- pass the semantic JSON validator with zero errors and zero warnings;
- build to SH3D archives with `Home.xml` as the first ZIP entry;
- pass ZIP CRC, XML parsing, model-reference, level-reference, and document-order
  inspection;
- generate SVG and PNG plan previews from the same canonical geometry.
- load successfully in the official WebGL viewer at the stored source camera,
  with no missing model faces or viewer errors.

The tests establish representation, compiler, and fast-render consistency. They
cannot prove that inferred dimensions match the imagined rooms. The source
images are AI-generated and may not correspond to one physically consistent
camera; final compatibility and high-quality lighting still require opening the
files in the user's installed Sweet Home 3D.
