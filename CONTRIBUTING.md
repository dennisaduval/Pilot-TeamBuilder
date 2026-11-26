# Development workflow

Use milestone feature branches and reviewed pull requests into main. Keep source, tests and reproducible build instructions under version control. Never commit customer images, credentials, production job settings or generated application bundles. Use semantic versions and update docs/DEVELOPMENT.md for every milestone. Validate offline operation and macOS Apple Silicon before a production release.

From the repository root:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```
