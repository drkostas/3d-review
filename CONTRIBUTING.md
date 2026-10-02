# Contributing

Issues and pull requests are welcome. If the bench shows a model wrongly, please attach the stages (or small meshes that show the same thing), the browser and its console output.

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test,penpot]'
.venv/bin/pytest
```

Please add a test for every change in behaviour. A new board measurement needs a test on a constructed case with a known answer, and a fault that turns it red.
