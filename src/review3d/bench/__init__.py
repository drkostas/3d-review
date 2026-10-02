"""The review bench, a web page for reviewing a 3D model.

A reviewer opens the page, picks a stage of the model, circles a spot or moves a ghost copy of a
part, writes what is wrong, and submits. Each submission is stored as a comment thread that the
assistant answers. Replies can carry a picture rendered from the reviewer's own camera.

`server.Bench` holds the plug-in points. They are the folders that hold the stage files, the
folder that holds the threads, the notifier used when the reviewer presses "Publish all", and the
optional rebuild and freshness hooks.
"""
