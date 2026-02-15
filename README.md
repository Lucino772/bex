# bex

> bex bootstraps Python virtual environments. You decide what runs next.

`bex` creates and manages Python virtual environments. Once the environment is ready, it runs a program inside it to do the actual work. That program can be anything, as long as it can be invoked from Python.

`bex` focuses on preparing the environment and making sure it can be created again in a consistent way. It does not define how work is structured or executed.

## How it works

When you run `bex`, it:

1. Creates or reuses a Python virtual environment
2. Installs dependencies
3. Executes a program inside that environment

`bex` builds on `uv` for environment creation and dependency management. It does not reimplement packaging or dependency resolution. Environments are reused when nothing has changed, and rebuilt when the configuration changes.
