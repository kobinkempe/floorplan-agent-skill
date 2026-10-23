# SH3D export contract

The exporter declares Sweet Home 3D XML version `7000`. The XML-only archive
mechanism requires Sweet Home 3D 5.3 or newer, but releases older than 7.0 may
show a forward-version warning.

## Archive

An output `.sh3d` is a ZIP archive containing:

- `Home.xml` as the first entry;
- procedural OBJ assets listed by `MODEL_FILES` in `floorplan_tool.py`, including
  primitives, openings, furniture proxies, lights, fireplaces, and railings.

The exporter intentionally omits the legacy Java-serialized `Home` entry. Sweet Home 3D versions before 5.3 are unsupported.

## XML ordering

Keep root children in Sweet Home 3D DTD order:

1. `property`
2. `environment`
3. cameras (`camera`, `observerCamera`)
4. `level`
5. furniture elements (`doorOrWindow`, `pieceOfFurniture`, `light`)
6. `wall`
7. `room`

Changing this order may make the file unreadable.

## Geometry

- SH3D distances are centimeters.
- Wall coordinates are centerlines.
- Opening X/Y is computed from its host wall and center offset.
- Opening width follows the host wall direction.
- Procedural OBJ models use X for width, Y for height, and Z for depth. They use
  a unit bounding box and are scaled by SH3D.
- Every procedural face is emitted in both winding directions. Do not rely on
  `backFaceShown`; some SH3D builds otherwise cull visible faces.
- A canonical railing wall is exported as an open post-and-rail furniture model,
  not as an opaque wall.
- Room polygons control floor and ceiling surfaces.
- XML colors are eight-digit uppercase ARGB hexadecimal (`FFRRGGBB` for opaque
  surfaces). Decimal color strings are invalid for this exporter subset because
  Sweet Home 3D parses color attributes as hexadecimal.
- The exporter always writes an explicit southeast aerial `topCamera` sized from
  the complete project bounds. A selected observer camera becomes the active
  `observerCamera`; every canonical camera is also written as a stored point of
  view.
- Camera target conversion follows Sweet Home 3D's plan convention: the forward
  vector is `(-sin(yaw), cos(yaw))`; yaw 0 faces plan-south.
- Light objects contain a nested normalized `lightSource` and explicit power.

## Validation

`inspect` must verify:

- ZIP integrity;
- `Home.xml` existence and XML parsing;
- root element `home`;
- every `model` reference exists in the archive;
- every XML level reference resolves;
- root-child ordering follows the supported subset.
