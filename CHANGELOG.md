# Changelog

## Unreleased

- Reply pictures line up with the reviewer's picture. `render_reply` uses the lens (`view.fov`) and the frame (`view.width` by `view.height`, scaled down to at most 1600 px wide) stored with the submission, instead of a fixed lens and size. It also applies each GLB node's transform and the "apart" slider of the parts stage, as the page does, draws the number labels, and draws on the page's background colour.
- The picture shadow. Without an old mesh, `say` and `render_reply` compare the reviewer's before picture with the new render and paint in orange what was model then and is empty space now (`shadow_picture=True` by default). `thread.picture_shadow(before, after)` does the same for any two pictures.
- `say` and `render_reply` take `shift=(x, y, z)`, which moves the stored camera and circle to a stage file whose frame moved since the submission (a build that re-centres the scene, for example).
- `thread.read_png` reads 8-bit PNG files with the standard library and numpy, and `thread.reply_frame(submission)` gives the lens and size a reply uses.
- The bench names a turn's axis (`turn_axis_named`) only when the turn is within 2 degrees of it. The bar was 14 degrees, so a turn with a visible tilt in it was labelled "world z" while `turn_axis` held the tilted axis. Any other turn reads "a free axis", with the nearest named axis and how far from it, for example "a free axis, 11° from world z".

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
