# The review loop: what to fix, in what order, and when to stop

A reconstruction is never finished, only stopped. This is the order to work in
and the rule for stopping, so that effort lands on what a viewer actually
notices rather than on whatever is easiest to adjust.

**The governing rule: do not spend a cycle on tier N+1 while tier N is
failing.** Detail applied over a wrong camera or a half-size fireplace is
wasted — it has to be redone once the thing beneath it moves.

## Tier 0 · Structure

`validate` reports zero errors and zero warnings, and the archive opens in the
Sweet Home 3D desktop application. Not negotiable, and nothing else is worth
measuring until it holds.

## Tier 1 · Overall resemblance

Before measuring anything, look at both images and answer three questions:

1. **Would a person describe them the same way in one sentence?**
   "A double-height Art Deco living room with a huge fireplace between two tall
   windows" should fit both, or neither.
2. **Do they share the notable features?** List the four or five things you
   would mention when describing the room to someone who cannot see it. Every
   item on the reference's list should appear on the render's.
3. **Would someone shown both agree they depict the same room?**

This tier is a diagnosis, not a task. You never fix "overall" directly — a
failure here routes to a lower tier, and the routing is the useful part:

| What is missing from the description | Fix in |
| --- | --- |
| Wrong scale, wrong shape, wrong ceiling, wrong viewpoint | Tier 2 |
| A named feature absent — fireplace, gallery, bay window, staircase | Tier 3 |
| The room's purpose unreadable: no table in a dining room | Tier 4 |
| Right things, wrong mood — too bright, too flat, wrong palette | Tier 5 |

Re-ask these three questions at the end of every cycle. They are also the only
honest test of whether the last change helped.

## Tier 2 · Envelope and camera

The room itself and the viewpoint onto it. A wrong camera makes correct
geometry look broken, so rule it in or out before suspecting the model.

- Room proportions and ceiling height
- Level structure: double height, mezzanines, changes of floor level
- Camera position, direction and lens

**Passes when** the back wall fills the frame within ±8% of the reference's
share, the ceiling and floor junctions sit within ±5% of the frame height, and
the room's plan aspect ratio is within ±15%.

Budget: 3 cycles.

## Tier 3 · Fixed architecture

Everything a builder would have installed. These are the features people name,
so they outrank all furniture.

In priority order:

1. **Openings** — windows and doors: how many, where along their wall, how
   large, what proportion. A missing window is worse than a mis-sized one; a
   window on the wrong wall is worse than either.
2. **Structural features** — galleries, balconies, staircases, columns,
   changes in ceiling height, arches.
3. **Built-in runs** — kitchen cabinetry, bookcase walls, fireplaces,
   panelling, bar niches.
4. **Ceiling and floor treatment** — coffering, beams, crown mouldings, inlays.

**Passes when** every feature in the reference is present, each within ±15% of
its size and ±8% of its position along its wall.

Budget: 3 cycles.

## Tier 4 · Major furniture

Furniture is judged on a strict internal order. Each level matters more than
everything below it, and this is where most misplaced effort goes: refining the
shape of a sofa that faces the wrong way is worth nothing.

1. **Presence** — is the piece there at all
2. **Class** — is it the right kind of thing: a sofa where a sofa belongs
3. **Direction** — does it face the right way. Check this explicitly and
   per piece; do not assume it from the fact that the room looks plausible.
   Declare `facing` on everything with a front, run `apply-facing`, and look at
   the arrows in the 2D plan. Half a room can be backwards without anything in
   a render looking obviously broken
4. **Position and footprint** — right place, roughly right size
5. **Height class** — low, counter, or tall
6. **Shape and style** — the actual silhouette. Lowest. A box in the right
   place beats a beautifully modelled piece in the wrong one.

Pieces are ranked too, and the ranking beats the checklist: **a primary piece
facing the wrong way outranks every problem with a secondary one.**

| Rank | What | Examples |
| --- | --- | --- |
| **Primary** | Defines the room's purpose, or occupies more than about 15% of the frame | Dining table, sofas, bed, kitchen island, banquette, bookcase runs, large rug |
| **Secondary** | Supports a primary piece, or reads clearly but is not the subject | Chairs round a table, armchairs, pendant over an island, sideboard |
| **Tertiary** | Small, incidental, often reasonably left out | End tables, lamps, stools, objects on surfaces |

**Never add a tertiary piece while a primary one is wrong.** If the budget runs
out with primaries correct and tertiaries missing, that is a good outcome —
omission reads as simplification, while a wrong primary reads as a mistake.

**Passes when** every primary and secondary piece is present, facing correctly,
within ±30% of its size and ±10% of its position in the frame.

Budget: 2 cycles.

## Tier 5 · Colour and light

Exposure first, always. A render that is 30 levels brighter than its reference
has an exposure fault, and every material will look wrong until it is fixed —
editing individual colours first means undoing that work later.

1. Overall exposure: `environment.light_color`
2. Large surfaces: floor, walls, ceiling
3. Major furniture colours
4. Accent colours

**Passes when** mean luminance is within 20 levels of the reference and neither
image has substantially more blown or crushed area than the other.

Budget: 1 cycle.

## Tier 6 · Detail

Mouldings, hardware, seams, patterns. Optional, and the first thing to drop.

Budget: 0–1 cycles.

## Stopping

1. **A lower-tier regression reverts the change.** If a tier-4 edit pushes a
   tier-2 measurement out of tolerance, undo it. Check this every cycle; it is
   cheap and it prevents slow drift.
2. **Stop the job when tiers 0–4 pass and a full cycle promotes nothing.** No
   tier moving from fail to pass in a whole pass means the edits have become
   guesses.
3. **Stop a tier when its budget is spent.** Record the residual as a known gap
   and move on. Do not borrow from the next tier.
4. **Hard cap of 10 render cycles**, whatever the state. Report the tier
   reached.
5. **Never iterate on what the format cannot express.** The exporter emits flat
   colours, so patterns, textures, veining, wood grain, printed book spines and
   fabric weave are out of reach. Name them once as out of scope. This is the
   trap that consumes an unbounded number of cycles, because each attempt looks
   different without getting closer.
6. **When the source is internally inconsistent, declare and move on.** Some
   images cannot be produced by any single lens — a foreground object may be
   drawn smaller than its position allows. Anchor to the wall, because it is
   tied to objects of known size, state the compromise in the assumptions, and
   stop trying to satisfy both.

## Translating a finding into an edit

A reviewer reports in image terms — "too large", "too far left", "needs to be
further away". Converting one into a plan coordinate needs the camera's facing,
and getting the sign wrong makes the render worse in a way that looks like the
finding was mistaken.

Work out which way is *away* before editing. A camera at `y = 830` looking along
decreasing y means a piece moves away by **decreasing** its `y_cm`. In this
project's coordinates, y increases toward the south, so a north-facing camera
has "further" and "larger y" pointing in opposite directions. One review here
recommended moving an island from y=430 to y=472 to push it back; that moved it
70 cm closer and it rendered larger still.

The cheap check: after any positional edit, confirm the piece moved the way you
intended before judging anything else about it.

**Verify an orientation convention on a piece with an unmistakable front.** A
bookcase against a wall is ideal: at the right angle its shelves face the room,
at the wrong one the room sees a flat back panel, and no amount of squinting is
needed. A sofa is a poor test — from an elevated camera its tall back and its
arms swap apparent order, so both orientations look plausible, and a whole
project can be built on the wrong reading.

Two related traps:

- **A room floor gains more light than furniture standing on it**, so a floor
  colour that looks correct in isolation can clip to pure white near the
  camera while every object on it stays in range. Fix by darkening the floor,
  not by dimming the room.
- **A light source placed inside a solid never shows.** A lit cabinet interior
  has to sit proud of the cabinet's face, not within its volume.

## Reporting where you stopped

Say which tier was reached and what remains, per room. "Tiers 0–4 pass; tier 5
partial; no crown moulding and no rug pattern" is a useful statement. Silence
about the gaps is not, and neither is presenting a tier-3 result as finished
work.

## Getting an independent read

Judging your own reconstruction is unreliable — you know what each shape was
meant to be, so you see the intent rather than the image. Have a reviewer with
no knowledge of the build compare the two pictures.

`reviewer-prompt.md` holds a prompt for this. Run it at the end of tier 2 and
again at the end of tier 4: once to confirm the room and viewpoint before
anything is built on them, and once to check the result. Give the reviewer
`scripts/compare_render.py` so it measures rather than builds measuring tools.
