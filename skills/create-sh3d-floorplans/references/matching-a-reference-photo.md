# Getting sizes out of a reference photograph

Guessing dimensions produces a room that is recognisably *a* room and
recognisably not *the* room. Every number below comes from measuring pixels in
the source and dividing by something whose real size is known. Do this before
authoring, not after the first render disappoints.

Measure in whatever way is convenient — reading coordinates off the image is
enough. Precision to about ten pixels is plenty; the method is what matters.

## 1. Find the scale at the back wall

Pick a feature whose real height is not in doubt and measure it in pixels.

| Feature | Real size |
| --- | --- |
| Kitchen counter top | 90 cm |
| Interior door head | 200–210 cm |
| Chair seat | 45 cm |
| Table top | 74–78 cm |
| Stair riser | 17–19 cm |
| Standard ceiling | 240–270 cm |

A counter top 157 px above the floor line is 157 / 90 = **1.74 px/cm at that
wall**. Everything else at the same distance divides by the same number:
a 1024 px frame is 588 cm wide there, and a wall filling 0.478 of the frame
height is 281 cm tall.

## 2. Size everything on that wall as a fraction of it

Ratios transfer even though the source camera is unknown, because both
measurements share one scale. For a wall modelled 850 cm wide:

```
fireplace mantel spans 287 px
back wall spans      685 px
                     -> 287 / 685 * 850 = 356 cm
```

This is how the living room's fireplace went from 205 cm — a guess that looked
plausible in isolation and half-size against the source — to 356 cm.

## 3. Solve the camera from two horizontal lines

Where a wall meets the floor and where it meets the ceiling are enough to pin
down eye height, pitch and field of view together, because both have known
heights at a known distance:

```
fraction_from_top(distance, height) =
    (tan(pitch + half_fov) - (height - eye) / distance)
    / (tan(pitch + half_fov) - tan(pitch - half_fov))
```

Read the two fractions off the source, substitute the wall's height and
distance, and solve. The living room's ceiling junction at 0.21 and floor
junction at 0.79 gave a 205 cm eye, +5° of pitch and a 76° field of view. Use
`camera-fit` when you only care about how much of the frame a wall fills;
use this when the vertical framing matters too.

## 4. Place furniture by inverting the same formula

Measure where a piece's top edge appears, and the formula returns the distance
it must stand at. The living room's four armchairs, two sofas and coffee table
were all placed this way in one pass — no iteration on depth at all.

Horizontal position works the same way: at distance *d* the frame is
`2 * d * tan(half_fov)` wide, so a piece centred at image fraction *f* sits at
`camera_x + (2f - 1) * d * tan(half_fov)`.

**Use the piece's near face, not its centre.** A 92 cm deep chair whose centre
is 272 cm away presents its front at 226 cm and renders about 1.3× larger than
the centre distance predicts.

## 5. Expect the source to contradict itself

Generated and heavily retouched images frequently are not consistent with any
single lens. The kitchen source puts a 200 cm island in 320 px, which would
need the frame to be 640 cm wide where the island stands — wider than at the
back wall behind it. No pinhole camera can do that.

When that happens, **trust the wall**. It is anchored to objects of known size,
it is what the eye reads the room's proportions from, and it is the part a
viewer will compare. Then place foreground pieces at the depth that lands them
in the right part of the frame, accept that they come out somewhat larger than
the source shows them, and say so in the assumptions rather than quietly
distorting the room to hide it.

## 6. Read the render, not your intent

Sample actual pixels before concluding anything about colour:

- A room floor renders at roughly its own colour. A piece of furniture standing
  on it renders at about **0.8** of its colour. A rug therefore vanishes unless
  its colour differs from the floor by more than that gap — `#CFC8B8` on
  `#9E9689` came out as (171,161,146) against (160,152,136).
- Blown-out white is an exposure problem, not a colour problem. Check
  `environment.light_color` before editing any material.
- Calibrate any threshold you invent. A "saturated pixel" test that excludes
  mid-tone browns will report a fully coloured render as empty.

## 7. Check that detailing survives the stretch

Models are written in a unit cube and scaled per axis, so anything expressed as
a fraction stretches with the piece. `window` and `wall-panel` compensate
automatically. If you add a generator with fine detail, give it the same
treatment — see `ASPECT_AWARE_SHAPES`.

Composite parts must never share a plane, either: the exporter sets
`backFaceShown`, so two coplanar faces both get drawn and band the surface with
stripes. Run `check-models` after touching any generator.

```powershell
python scripts/floorplan_tool.py check-models
```

## 8. Match the model's own geometry

Detail that decorates another model has to line up with what that model
actually generates. The bookcase puts its shelves at 0.22, 0.40, 0.58 and 0.76
of the carcass with a 0.08 base — so on a 275 cm case the usable bands begin at
22, 68, 117, 167 and 216 cm. Book rows placed on a round 54 cm pitch floated
between the shelves and read as scattered blocks instead of packed spines.
Read the generator before choosing elevations.
