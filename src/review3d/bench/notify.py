"""Notifiers, which tell the assistant that the reviewer pressed "Publish all".

A notifier is any object with a method `notify(text)` that returns `(ok, detail)`. The default
appends a line to a log file, which works on every machine and needs nothing installed. A project
with a better channel (a chat it can write into, a message queue) passes its own notifier.
"""
import datetime as _dt
import pathlib


class FileNotifier:
    """Append each notification to a log file, one line per notification, with a timestamp."""

    def __init__(self, path):
        self.path = pathlib.Path(path)

    def notify(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp}  {text}\n")
        return True, f"written to {self.path}"
