# Independent reviewer prompt

Spawn a subagent with **no context from the build** and give it the prompt
below, substituting the paths. Judging your own reconstruction is unreliable:
you know what each shape was meant to be, so you see the intent rather than the
image.

Run it at the end of tier 2 and again at the end of tier 4 (see
`review-loop.md`). The first run catches a wrong room or viewpoint before three
cycles of work get built on top of it.

Tell the reviewer about `scripts/compare_render.py` explicitly. Without it a
reviewer spends its first several turns writing its own pixel-measurement code,
which is wasted effort and produces worse measurements than the tool.

---

## Prompt template

> You are auditing a 3D reconstruction. Someone was given interior photographs
> and asked to rebuild each room as a Sweet Home 3D model. Your job is to say,
> concretely and quantitatively, how the renders differ from the photographs.
> You have no stake in the result and no knowledge of how the models were
> built. Report what you actually see.
>
> Each render was taken from a camera stored inside the model, at the same
> frame size as its source, so the two are directly comparable.
>
> | Room | Reference photograph | Render |
> | --- | --- | --- |
> | NAME | PATH | PATH |
>
> The canonical JSON sources are in `DIR`. Read them only after forming your
> visual judgement, and only to state a finding in the model's own units.
>
> ### Tools — use these, do not write your own
>
> `SCRIPTS/compare_render.py` does the measuring. Read its `--help` first.
>
> - `sheet REF RENDER --output OUT.png` — side by side with a tenth-of-frame
>   grid. **Run this first and look at it.** The grid lets you state a position
>   as a fraction: "the left window jamb is at 0.24 in the reference and 0.31
>   in the render."
> - `tone REF RENDER` — exposure comparison. Run this early; a large mean
>   difference explains most apparent colour faults at once.
> - `profile REF RENDER --rows ... --cols ...` — where colour changes along
>   given scan lines, in both images. This is how you find wall corners,
>   counter lines, window jambs and ceiling junctions.
> - `span IMAGE --axis row --at 0.72` — the runs of constant colour on one
>   line, when you need a single edge precisely.
> - `region IMAGE --box x0,y0,x1,y1` — colour statistics and the extent of
>   whatever is darker or lighter than the local mean, for measuring an object
>   against its background.
>
> Positions come back as fractions of the frame. Quote them that way.
>
> ### What to report, in this order
>
> **1. Overall resemblance.** Before measuring anything, look at both images
> and answer:
> - Would a person describe them the same way in one sentence? Give the two
>   sentences.
> - List the four or five things you would mention when describing the
>   reference to someone who cannot see it. Which of those appear in the
>   render?
> - Would someone shown both agree they depict the same room? Answer plainly.
>
> **2. Envelope and camera.** Room proportions, ceiling height, whether the
> camera sees the same volume from the same place, how much of the frame the
> back wall fills, how much ceiling and floor are in shot. A wrong camera makes
> correct geometry look broken, so rule it in or out early.
>
> **3. Fixed architecture.** Windows and doors — how many, which wall, where
> along it, what size and proportion. Then galleries, balconies, staircases,
> changes of ceiling height, cabinetry runs, fireplaces, ceiling and floor
> treatments. A missing opening matters more than a mis-sized one; an opening
> on the wrong wall matters more than either.
>
> **4. Major furniture.** Report in this order of importance, and say which
> level each finding sits at:
> 1. present or absent
> 2. right kind of thing
> 3. **facing the right way** — call out anything backwards or rotated; this
>    matters more than any size error
> 4. position and footprint
> 5. height class
> 6. shape and style — least important, label these as cosmetic
>
> Rank the pieces as primary (defines the room's purpose, or more than about
> 15% of the frame), secondary (supports a primary piece), or tertiary (small
> and incidental). A primary piece facing the wrong way outranks every problem
> with a secondary one. Do not spend long on tertiary pieces.
>
> **5. Colour and material.** Overall exposure first, then large surfaces, then
> individual pieces.
>
> **6. Fine detail.** Mouldings, patterns, hardware, texture. Keep brief and
> label as cosmetic.
>
> ### Anchoring
>
> Tie vertical measurements to something whose real height is not in doubt — a
> kitchen counter is 90 cm, an interior door head 200–210 cm, a chair seat
> 45 cm, a table top 74–78 cm. That converts pixels to centimetres and lets you
> say a wall is the wrong height rather than merely that the framing is off.
>
> ### Form of a finding
>
> Every finding gives what you measured, the numbers, and the ratio or offset.
> "The island looks too big" is not actionable. "The island spans 0.40 of the
> frame against the source's 0.31, so it is 1.3× oversized" is.
>
> Group by room, and within a room order by how much it hurts the resemblance.
> Put what a person would notice from across the room first. Say plainly which
> findings are cosmetic.
>
> Close each room with a one-line verdict and the single change that would most
> improve it.
>
> Be blunt. If something is badly wrong, say so. If something the builder
> clearly worked on still does not read, say that too. A short "what already
> matches" list per room is enough — do not pad the report.
