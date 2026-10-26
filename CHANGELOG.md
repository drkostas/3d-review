# Changelog

## Unreleased

- `review3d.fresh` tells whether a picture or a stage file shows the current model, by content and not by date. `import_closure` reads the source files a builder reaches by import (lazy and relative imports and parent packages included), `fingerprint` hashes them in an order that depends only on names and contents, `Stamps` records which fingerprint made each picture, `verdict` lets the content check outrank the clock both ways, `Stale` carries the reason, and `lock` keeps one rebuild running at a time and frees itself when its holder died.
- `review3d.bench.watch.Watcher` rebuilds what has fallen behind in the background, waits while the code is being edited or a hold file is fresh, and serves its `status` at `/api/fresh`. `run_step` runs a rebuild step in its own process group and `stop_steps` ends them.
- `say` passes `render_reply`'s options through (`fov`, `mark`, `size`, `stage`, `labels`) and takes `caption`, so a closer reply, or one without a circle that no longer marks anything, is one call and is captioned for what it shows.
- The skill's section on the first print says that nothing stays at one line and that this is not a question for the person: the thin end is changed, measured and shown.
- The skill has a section on what to check before the first print: every wall as the part lies on the plate, the exported file read back as one solid, the slice read back against the saved profile, and how to show a thickening and ask about what is left. Two rows join its failure catalogue.
- Who may post. `Bench(may_post=)` is asked before every POST (submit, comment, publish, rebuild and a project's own routes) and refuses the post with 403 and its reason. `review3d.bench.access.tailnet_users(logins, devices=None)` is one for a bench reached over Tailscale: a program on the same machine may post, a request through Tailscale Serve is judged by the login Serve sets, a direct Tailscale caller by `tailscale whois` of its address, and tagged devices and other addresses are refused. `post_from`, `post_devices` and `tailscale` in the settings file turn it on. What the reviewer posts reaches the assistant as the reviewer's words, so on a bench others can reach this decides who may speak for the reviewer.
- The bench can listen on several addresses. `host` in the settings file (or `--host` given more than once) takes a list, for example `["127.0.0.1", "192.0.2.7"]`, so a bench with no login answers on this machine and on one other address of it, such as a VPN address, and not to other devices on the same local network. An address that is not on the machine yet, such as a VPN address when the bench starts before the VPN, is tried again in the background until it binds.
- `thread.append(..., caption=)` gives a reply picture its own caption. The threads page shows it under the picture, and keeps its usual line (the reviewer's camera, the old position in orange) for a picture without one, so a close-up or a cut-away is no longer described as a view from the reviewer's camera.
- Reply pictures line up with the reviewer's picture. `render_reply` uses the lens (`view.fov`) and the frame (`view.width` by `view.height`, scaled down to at most 1600 px wide) stored with the submission, instead of a fixed lens and size. It also applies each GLB node's transform and the "apart" slider of the parts stage, as the page does, draws the number labels, and draws on the page's background colour.
- The picture shadow. Without an old mesh, `say` and `render_reply` compare the reviewer's before picture with the new render and paint in orange what was model then and is empty space now (`shadow_picture=True` by default). `thread.picture_shadow(before, after)` does the same for any two pictures.
- `say` and `render_reply` take `shift=(x, y, z)`, which moves the stored camera and circle to a stage file whose frame moved since the submission (a build that re-centres the scene, for example).
- `thread.read_png` reads 8-bit PNG files with the standard library and numpy, and `thread.reply_frame(submission)` gives the lens and size a reply uses.
- The bench names a turn's axis (`turn_axis_named`) only when the turn is within 2 degrees of it. The bar was 14 degrees, so a turn with a visible tilt in it was labelled "world z" while `turn_axis` held the tilted axis. Any other turn reads "a free axis", with the nearest named axis and how far from it, for example "a free axis, 11° from world z".
- `write_stages(pivots=, carries=)`. `pivots` gives the point a part turns about on the bench instead of the middle of its box (a hand turns about its wrist), in one stage's coordinates, and each other stage gets it moved with the part. `carries` names the parts that move and turn with it (a cup in a hand), drawn as ghosts with it. The submission still names only the picked part, and `centre_after` is that part's own centre.

## 0.1.1

- stages.json can name the order of the stages, a label for each, and the stage the bench opens on (`write_stages(labels=, order=, default=)`)

## 0.1.0

First release.

- The review bench (`3d-review bench`), with threads, replies drawn from the reviewer's camera and "Publish all"
- `stages.write_stages` and `3d-review stages` to put any meshes on the bench
- The step ledger, which records which pipeline step moved or rebuilt a watched part
- The requirement board, with fault injection that proves each row can go red, and general shape measurements
- The Penpot bridge for 2D outlines in millimetres
- A Claude Code skill, installed with `3d-review skill`
