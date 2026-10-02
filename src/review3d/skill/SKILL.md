---
name: 3d-review
description: Use when reviewing a 3D design together with a person, through the 3d-review package (imported as review3d). Covers writing the person's requirements as board rows and proving each row can go red with a planted fault, building stages and serving the review bench, reading submissions after Publish all and answering every thread with a picture from the person's own camera, turning a moved or turned part into a lock that survives rebuilds and proving it with the step ledger, asking questions with pictures instead of prose, keeping the pictures current, the Penpot bridge for 2D outlines, and the failures that taught each rule.
---

# Reviewing a 3D design with a person

The person can see what is wrong long before they can say it in words. The bench lets them show it
on the model, the board turns what they asked for into checks that can fail, and the ledger proves
the build kept what they fixed. Your job is to keep all three honest. Every rule below exists
because a green check, a served picture or a confident sentence was once wrong while looking right.

Two rules sit above the rest.

1. The person validates and the machine only verifies. A row a script can fail on is a
   verification. A judgement only the person can make is a validation, and passing every
   verification is never "done".
2. A green row means nothing until you have seen it go red on purpose.

## The loop, in order

1. Read the project's record (its board, its open threads, its notes) before acting on any
   standing list of tasks. A list is older than the decisions made since, and acting on it can
   undo the person's own choices while looking diligent.
2. Write every requirement as a board row, in the person's words, before building anything.
3. Give every measured row a fault and run `Board.prove`. Fix the rows that are not proven.
4. Build every stage the design passes through and serve the bench.
5. Wait for "Publish all". Read every submission, both pictures first, then the words, then the
   numbers.
6. Answer each thread with a picture rendered from the person's camera.
7. Turn each move, turn or size into a lock. Rebuild, then prove with the ledger that no later
   step took it back.
8. Rebuild the stages and every picture, rerun the board and the proofs, and open what you made.
9. Ask anything that needs the person with pictures on the bench, never as prose options.
10. Go through the checklist at the end before calling anything done.

## Stages and the bench

A stage is one view of the model made of named parts, for example "printed", "parts" and
"assembled". Show every stage the design passes through, so the person can check each conversion
rather than be told it passed. One picture of the finished thing is not enough.

```bash
pip install 3d-review
3d-review stages lid.stl box.stl hinge.stl -o model        # one stage called "parts"
cp "$(python -c 'import review3d,os;print(os.path.dirname(review3d.__file__))')/bench/bench.example.toml" model/bench.toml
3d-review bench --config model/bench.toml                  # then open /twist.html, threads at /threads.html
```

```python
from review3d import stages
stages.write_stages({"parts": laid_out, "assembled": assembled}, "model",
                    faces=(0, -1, 0),                       # the direction the model faces, sets the camera
                    offsets={n: laid_out[n].bounds.mean(0) - assembled[n].bounds.mean(0) for n in assembled})
```

- A stage named "parts" gets an "apart" slider when `offsets` is given.
- Parts named "base" or "no" plus digits (number labels) are drawn but cannot be picked.
- `stages.json` stores each part's principal axes, so the person can turn a part about its own
  length with the "Part" space. Without them "any axis" is impossible.
- The bench has no login. Keep `host = "127.0.0.1"` or a private network. The person often reads
  on a phone, so check every page at phone width too.
- For your own hooks, build `review3d.bench.server.Bench(model_dirs, subs_dir, stages_dir,
  notifier=..., rebuild=..., fresh=...)` and call `server.serve(bench, port, host)`. A notifier is
  any object whose `notify(text)` returns `(ok, detail)`. The default `FileNotifier` appends to
  `<subs_dir>/published/notifications.log`, so watch that file from a background task.

## Reading submissions

"Publish all" stamps every submission without a `published` stamp, writes
`submissions/published/publish-<stamp>.json` with the ids, and notifies you once. Read every id in
the manifest, not a sample.

```bash
cd model && python -m review3d.bench.thread --list        # state, id, replies, the words
```

Each `submissions/<id>.json` holds `kind` ("move" or "mark"), `stage`, `part`, `said`, `camera`
(`eye`, `at`), `view`, `shots` (`before`, `after` PNG names) and either `mark` (`part`, `at`,
`r_mm`) or `change` (`move_mm`, `move_len_mm`, `turn_deg`, `turn_axis`, `turn_axis_named`,
`space`, `scale`, `centre_before`, `centre_after`).

- Open both pictures before reading the numbers. The after picture hides the original and shows
  the ghost in its place, so flipping between the two shows only what changed.
- A move is relative to the build the person was looking at. `centre_after` is where they put the
  part, which stays right after the build changes. Use the place, not the delta.
- Several moves on one part do not add up. Each was made looking at a build that already carried
  the earlier ones, so the newest one is the whole answer.
- Read what the gesture was for. A person who drags a whole part forward may want one end of it
  forward and had no other control. Their words beside the gesture usually say which.

## Answering threads

Every submission is a task and the thread is where you work it with the person, many turns if
needed. Answer all of them, each with a picture.

```python
import trimesh
from review3d.bench import thread
thread.configure("model/submissions", "model/stages")
old = trimesh.load("model/stages/assembled.glb", force="scene").geometry["lid"].copy()  # before rebuilding
# ... change the model, rebuild the stages ...
thread.say(sid, "Turned the lid 15 degrees about its hinge as you showed. Orange is where it was.",
           state="fixed", shadow={"lid": old}, highlight="lid", did={"turn_deg": 15.0})
```

- `say` renders the stage file on disk from the camera stored with the submission, so the reply
  lines up with the person's own picture. Rebuild the stages first, or the reply shows the old model.
- Open every reply PNG before sending it. A reply picture that does not show the change answers
  nothing.
- States are `new` (waiting on you), `answered` (waiting on the person), `fixed` (done, with a
  measured change behind it) and `wont` (declined, with the reason written). A follow-up from the
  person reopens the thread as `new`.
- When you find you were wrong in an earlier reply, say so on that thread.

## Locks

A submission is a lock. Do not copy the person's changes into a second file, because two records of
one thing drift apart. Read `submissions/*.json` directly. The lock holds from the moment the person
presses Submit, not from the moment you agree. Only the person withdraws it. A mark is a comment,
not a lock, so answer it rather than enforce it.

- Refuse loudly, by name, any lock the build cannot keep. A lock on a part that the build merges
  into another part should name the part it became. A turn on a part the build regenerates from
  parameters every run cannot survive, so refuse it or turn it into what can survive (a size on a
  regenerated tube becomes its thickness). A refused lock is a red row, never a footnote.
- Say which promise each lock gets. "Held" means nothing after the seam may change it. "Settled"
  means later fitting steps still get a say, and the remaining difference is reported.
- A part that carries another (a lid carrying a knob) turns its passenger with it. A turn the
  person set on the passenger was made relative to the carrier, so subtract the carrier's turn.
- The step that applies locks writes a note even when there is nothing to apply. A step that
  silently does nothing looks the same as a step that silently failed.
- Run the lock step once with a non-empty lock set before trusting it. A loop over an empty list
  can carry a fatal error for hours while every row stays green.

## The ledger

The ledger wraps every step of the build and records which step moved or rebuilt each watched part.
Steps are module functions called as `_name(out, ...)`, with the parts dict named `out`.

```python
import pathlib
from review3d import ledger
import mybuild

ledger.install(mybuild, {"lid", "knob"}, pipeline="build")
assert not ledger.missing(mybuild, "build")            # a step the ledger cannot see is a hole
mybuild.build()
ledger.write(pathlib.Path("model/ledger.json"))
late = ledger.after_seam("lid", "_hold_locks")           # every touch after the lock step
```

- Put the seam (the step that applies locks) where measurement says, not where it seems right.
  Install the ledger, build once, and find the last step that rotates or scales each part. A lock
  applied before that step is overwritten.
- Make the seam its own `_step(out, ...)` so the ledger wraps it. For inline code that moves a
  watched part on purpose, call `ledger.mark(out, "label", parts)` so it is logged as `asserted`
  rather than reported as an unexplained move.
- Read the file it writes once. `steps_run` must equal the length of one run. The pipeline wrapper
  resets the ledger on each call, so call the pipeline through the module.
- Moves are exact only while vertex order survives. A part whose vertex count changed reads
  "rebuilt", which says nothing about whether the turn survived. Check such a part with a signed
  property of its own (which way a marked face points).
- "Nobody watched" outranks "nothing happened". An unwrapped step or a missing ledger is red.

## The board and fault proofs

```python
from review3d.board import Board, Row, at_most, between, gap, mirror_gap

board = Board()
board.add(Row("lid flush", "the lid sits flush, no gap you can see",
              measure=lambda d: gap(d.parts["lid"], d.parts["box"]), bar=at_most(0.3),
              unit="mm", look_at="assembled stage, front view", group="fit"))
board.add(Row("reads as the sketch", "it looks like the drawing", look_at="assembled stage"))

@board.fault("lid flush")
def lift_lid(d):
    d.parts["lid"].apply_translation([0, 0, 2.0])

board.report(design)
print(Board.proof_table(board.prove(design)))
```

- `requirement` holds the person's words. `look_at` names a picture the person can open, and that
  picture must answer the row.
- A row with no measurement stays unknown. Unknown is never green, and TODO is not pass.
- A choice the person made from a picture becomes a measured row with a two-sided bar (`between`),
  set from real builds of the options either side, so it goes red if anything flattens the choice
  or overshoots it.
- Read the healthy and faulted values in the proof table. A fault that reddens its row by a
  hundredth of the bar will stop testing after the next small change. Make the fault a clear defect.
- `also_red` is a finding. A row that turns red for another row's fault measures more than its name.
- `missed` with "the value did not move" means the row reads something the fault never touched. The
  usual cause is a second copy of the part (a cache, an earlier version) that the fault did not edit.
  The fault must change every copy any row reads.
- Run the real board inside the fault test. A copy of the arithmetic drifts from the original.
- When the harness says a row missed, read which rows actually flipped before rewriting the row. A
  harness matching on the wrong field reports a working row as broken.
- If the suite has a countable set of faults, assert the count, and check it the way the script
  runs, not by importing it (an import never runs the `__main__` block).

## Measuring honestly

Your eye is reliable about shape and absence, unreliable about contact, bulges and surface. Say what
the pixels show, then measure before changing anything.

- Measure the solid, not a convex hull or bounding box. A hull fills sockets and gaps and reports a
  part in open air as seated (`gap`, `penetration` and `distance_to_surface` use the solid).
- Sample the surface, not the vertex list. Vertices crowd where a mesh is finely detailed and pull
  a centre or an axis toward the detail.
- Measure a part before its hardware (holes, posts) is added, or the row reports the hardware.
- A world-axis box misreads a turned part by up to 40%. Use `length_along_axis`.
- An axis from principal components has no sign. Which way something points needs a marked feature.
- A name is not a direction and a centroid is not a centre. When a sign is in doubt, do it both ways
  and keep the one whose result is right. Do not reason about signs.
- Render a downloaded mesh along each axis before measuring it, and say which way is up.
- A sheet that frames each part in its own axes hides a part placed wrongly. Show placement in world
  orientation, as the part sits on the assembled model, and say which frame each caption uses.
- Facets, burrs and spikes may be the shading. Render the same mesh two ways before calling a
  defect, and check the scale (a facet sinks L squared over 8r below a true sphere).
- Small tiles of a whole model cannot separate bent from foreshortened, or touching from in front
  of. Judge a part from a picture made for it, at full scale.
- Pick the reference by name and run a new check against both candidate references once, to see
  which one makes it mean what its sentence says.

## Asking with pictures

A question in prose asks the person to imagine two shapes and compare them in their head. Put it on
the bench instead.

- If the person named or implied a reference, find it, put your model beside it and measure before
  asking anything. Ask only what the comparison cannot settle.
- Render each candidate answer. Build every option the same way, through the real build, so the only
  difference is the thing in question and the option they pick is the one they get.
- Put the numbers in the picture's caption, computed in the builder. A number printed to a log
  reaches no one, and a typed number goes stale within the hour.
- Open the picture at phone size before asking. Black views, stale views, a part forty pixels long,
  no link, a link that 404s, and a picture saying nothing have all reached the person before.
- When a caption says something is missing, give the reason, so it does not read as a mistake.

## Keeping the pictures current

- A picture is stale when anything that decides it changed, the model, the renderer, the template or
  the script that builds it. Hash all of them, by content, not modification time. A copied-back file
  keeps its content and changes its time, and the reverse also happens.
- Sort the files by full path before hashing, then confirm the hash matches in two processes with
  different `PYTHONHASHSEED` values. A sort by bare file name is random once two files share a name.
- Rebuild the geometry before the pictures of it. Re-rendering a picture from a stale stage gives a
  new picture of the old model.
- A full refresh can take minutes. Rebuild in the background and let `fresh` report what is behind.
- Before restarting a bench that spawns rebuilds, stop its child processes too. An orphaned child
  keeps holding the rebuild lock and every later rebuild is skipped.

## If you change the bench page

The page keeps `[hidden] { display: none !important; }` because an id rule with `display` beats the
browser's own rule. Other faults found by testing it rather than reading it. A canvas needs
`preserveDrawingBuffer` or the submitted pictures are blank. The gizmo's rings must be hidden before
each shot. Snap must round the move, not the absolute position. "Put it back" returns to the part's
home rotation. Labels must not be pickable. Click every control after a change and assert something
visible moved, and re-query elements after each action, because a test that keeps references across
a re-render measures its own past.

## The Penpot bridge

For flat outlines the person edits by hand. One unit is one millimetre. With `y_up=True` the y axis
is flipped on the way in and back on the way out. Outlines are written as native path shapes, so the
person has nothing to import.

```python
from review3d.penpot import PenpotClient, PenpotSettings, arrange, apply_cuts
client = PenpotClient(PenpotSettings.load("penpot.toml"))     # or PENPOT_* environment variables
result = client.push(arrange(outlines), file_name="Parts for review")   # result.url to open
edited = client.pull(file_id=result.file_id, cuts=True)       # shapes named cut... divide the others
```

Keep credentials in the settings file or the environment, never in code. `pull(via="export")` needs
Penpot's exporter service and is much slower.

## Failure catalogue

| Symptom | Cause | Check | Fix |
|---|---|---|---|
| The person says a part they placed went back | a later step moved or rebuilt it | `after_seam`, the ledger entries | move the seam after the last step that touches it, or refuse the lock by name |
| "No step touched it" but it moved | inline code between two steps | ledger entries with `inline` | let `install` read the steps, `mark` the labelled code |
| A seam never fired | empty lock list hid an error | run with one real lock | keep that run as a test |
| Two moves put a part in the wrong place | deltas summed on a changed build | `centre_after` of the newest move | use the newest place |
| Board all green, the person says it is wrong | the row measures something next to its claim | prove it, read healthy and faulted values | measure the solid, the right copy, the right frame |
| A row is proven by a hair | fault too small | margin in the proof table | plant a clear defect |
| A value does not move across a real change | the code is not running or reads another copy | print the value in a five-line probe | read the copy the build uses |
| Site says current, picture is old | freshness covers data only, or time beats content | change the renderer, it must go stale | hash everything that decides the picture |
| Pictures rebuild for ever | file order random in the hash | hash in two processes | sort by full path |
| Rebuilds skipped after a restart | orphaned child holds the lock | the lock holder's parent is 1 | stop children before restarting |
| Reply picture shows the old model | stages not rebuilt before `say` | open the reply PNG | rebuild, then reply |
| The person cannot answer a question | prose, or a picture they cannot use | open it at phone width | one picture per option, built the same way |
| Fixed one side of a mirrored pair | fixed per row | render the pair together | apply the fix to both |
| Fixed a new element, not the named one | did not search | search for every instance | fix all, render the named element |
| Undid the person's own decision | acted on a stale task list | read the record first | the record wins, say so |
| Facets or spikes on a smooth part | shading, not geometry | render two ways | fix the renderer, keep the old method as a red case |

## Before calling a design done

- [ ] Every requirement the person stated is a row, in their words, with a `look_at` they can open.
- [ ] `Board.proof_table` shows every measured row proven, with a margin you would trust.
- [ ] No row is unknown that a measurement could settle. Person-only rows are listed as theirs.
- [ ] Every lock is held or refused by name, `missing` is empty, and `after_seam` shows no late touch.
- [ ] Every thread is answered with a picture from the person's camera, none left `new`.
- [ ] The stages and every picture were rebuilt from the current code, and you opened them.
- [ ] Every stage the design passes through is on the bench for the person to check.
- [ ] The person has validated what only they can judge. A green board alone is not done.
- [ ] Nothing was printed or sent to make until the person said so.
