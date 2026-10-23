# Image and description inference workflow

## Contents

- Establish observability
- Choose scale anchors
- Construct the simplest plan
- Model vertical structure
- Place openings and objects
- Infer and test the camera
- Audit uncertainty

## Establish observability

Inventory only directly visible facts first:

- room type and visible wall count;
- corners and wall continuations;
- doors, windows, openings, and their relative positions;
- floor changes, steps, galleries, balconies, and voids;
- ceiling form and approximate vertical proportions;
- furniture that provides scale cues.

List invisible perimeter segments, occluded openings, and ambiguous reflections as unknown. A single interior perspective is not a measured plan and cannot establish unseen balconies, rooms, or exterior walls.

## Choose scale anchors

Prefer explicit measurements. Otherwise use several ordinary objects and state the assumptions:

- interior door: commonly about 80–95 cm wide and 200–240 cm high;
- dining chair seat: roughly 45–50 cm high;
- base cabinet/counter: commonly about 60 cm deep and 90 cm high;
- table: infer only a range from seating count.

Do not derive exact dimensions from one assumed anchor. Round image estimates to sensible increments, usually 5 or 10 cm, and use confidence below 0.7.

## Construct the simplest plan

1. Select a rectangular or minimally articulated perimeter consistent with the view.
2. Place the observed wall as the reference edge.
3. Add unseen walls only to close the test room; mark them estimated.
4. Avoid inventing adjacent rooms unless the image clearly shows their connection.
5. Keep separate design alternatives as separate JSON files rather than hiding ambiguity in one plan.

## Model vertical structure

- Use the level ceiling height for ordinary rooms.
- For a double-height volume, override the enclosing wall heights.
- Create an upper level at the gallery floor elevation.
- Create only the visible gallery/balcony floor polygons.
- Add a `void` over the open volume.
- Use low `railing` walls along open gallery edges.
- For a coffered ceiling, record the ceiling height but treat decorative coffers as objects only when layout testing requires them.

## Place openings and objects

Place an opening by distance along its wall from the wall start, not by an independent X/Y coordinate. Validate that the opening fits and does not overlap another opening.

Start with objects as scale and circulation proxies. Promote only conspicuous
silhouettes—curved banquettes, rounded islands, chandeliers, fireplaces,
bookcases, or railings—to the matching procedural shape. Avoid adding product
detail that the source does not establish.

## Infer and test the camera

Treat camera fitting as a separate inverse-rendering problem:

1. Start with an observer at a plausible eye height, normally 140–170 cm.
2. Use visible symmetry and verticals to estimate the target point.
3. Begin near a 63 degree horizontal field of view, then widen only when the
   source contains both near foreground and high ceiling in a square frame.
4. Use `pinhole` for geometry comparison. Depth of field and fisheye effects can
   hide placement errors.
5. Store the source-match camera in JSON and render it. Compare silhouettes,
   occlusion order, vanishing direction, and image-edge cropping before tuning
   colors or ornament.

AI-generated reference images may combine inconsistent local perspective cues.
When no single physical camera reproduces every edge, preserve the room geometry
and record the camera fit as approximate instead of distorting the plan.

## Audit uncertainty

Before building:

- confirm every entity has evidence;
- lower confidence for occluded or perspective-distorted dimensions;
- ensure `provenance.assumptions` identifies every closed unseen perimeter;
- include a disclaimer for image-only projects;
- distinguish features absent from the image from features known not to exist.
