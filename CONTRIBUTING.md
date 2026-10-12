# Contributing

Issues and pull requests are welcome. If the bench shows a model wrongly, please attach the stages (or small meshes that show the same thing), the browser and its console output.

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test,penpot]'
.venv/bin/pytest
```

Please add a test for every change in behaviour. A new board measurement needs a test on a constructed case with a known answer, and a fault that turns it red.

## Writing for this repository

Everything here is public, including commit messages and pull request descriptions. Please write them for a user of the package. Do not name other projects, people or machines that use it, and keep real hostnames, IP addresses, paths and credentials out of code, tests and history (examples use `example.org` and `192.0.2.x`). A check on every pull request looks for terms that must not appear here.
