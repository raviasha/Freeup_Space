# Contributing to Freeup Space

Run the packaging tests from the plugin directory:

```bash
pytest tests/test_packaging.py -q
```

The plugin source lives under `src/freeup_space/`; keep the repository
marketplace source path (`./plugins/freeup-space`) intact when packaging or
copying this repository for release.
