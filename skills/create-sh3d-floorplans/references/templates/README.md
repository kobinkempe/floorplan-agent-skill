# Templates

Copy a template and replace its values instead of writing a document from
scratch. Every template here validates with zero errors and zero warnings, and
`scripts/test_floorplan_tool.py` enforces that, so a template is always a
working example of the current schema.

## `single-room.json`

One rectangular room, 600 cm wide (x) by 500 cm deep (y), 280 cm ceiling.

Walls run clockwise from the top-left corner, so `north-wall` is the far wall
and `south-wall` is the one behind the camera. In a single frontal photograph
the camera-side wall is never visible — keep its confidence low rather than
inventing detail for it.

It includes the pieces that are easy to forget: a stored `observer` camera tied
to a source image, a window with a muntin grid, a real `light` object with
`light_power`, and separate `observations` and `assumptions` in `provenance`.

### Geometry conventions the template encodes

- Distances are centimetres; JSON angles are clockwise degrees.
- `x` runs right/east, `y` runs down/south in plan view, `z` runs up.
- The space polygon is inset by **half the wall thickness** (7.5 cm here) so the
  floor meets the inside face of the walls.

### If you change a room dimension

Update the wall endpoints **and** the space polygon. They are independent, and
moving only the walls leaves the floor behind — the room then renders with a
hole and any camera standing in it fails validation with
`camera.outside-space`.

### Placing the camera

Do not guess. Solve it:

```powershell
python scripts/floorplan_tool.py camera-fit room.json --wall north-wall --fill 0.7 --eye 165 --apply source-match
```
