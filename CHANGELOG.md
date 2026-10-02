# Changelog

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
