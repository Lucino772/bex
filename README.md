# bex

`bex` sets up a Python virtual environment and then runs whatever program you’ve configured inside it. Its only job is to bootstrap that environment and make sure it can be recreated consistently. It doesn’t decide how your work is structured or executed, it simply gets everything ready and hands off control.

## How it works

When you run `bex`, it creates (or reuses) a Python virtual environment, installs the declared dependencies, and then runs the configured entrypoint inside that environment.

Under the hood, `bex` relies on `uv` for environment creation and dependency management rather than reimplementing packaging or resolution logic. Environments are reused when nothing has changed and rebuilt automatically when the configuration differs.

## CLI

`bex` exposes a small CLI surface (no subcommands). Any extra arguments are forwarded to the configured entrypoint.

### Options

| Flags                      |           Default           | Description                                                     |
| -------------------------- | :-------------------------: | --------------------------------------------------------------- |
| `--version`                |           `False`           | Print version information (Python and `bex` version) and exit.  |
| `-f`, `--file <path>`      | First file matching `bex.*` | Path to the configuration file.                                 |
| `-C`, `--directory <path>` |  Current working directory  | Directory used to resolve the configuration file.               |
| `-b`, `--bootstrap-only`   |           `False`           | Only bootstrap the environment (skip executing the entrypoint). |

### Passthrough arguments

You can optionally provide a command and arguments after the options, they are forwarded as extra arguments to the entrypoint:

```bash
bex -f bex.conf <command> [args...]
```

If `--file` is not provided, `bex` looks for the first file matching `bex.*` in the target directory.

## Configuration

`bex` reads its configuration from a bootstrap header at the top of a file:

```yaml
# /// bootstrap
# uv: "0.10.2"
# requires-python: ">=3.11,<3.12"
# requirements: |
#   some-package
#   another-package
# entrypoint: some.module:main
# ///
```

This header tells `bex` how to build the environment and which Python entrypoint to run once it’s ready.

### Fields

| Field             |     Default    | Description                                                                         |
| ----------------- | :------------: | ----------------------------------------------------------------------------------- |
| `uv`              | Latest version | Version of `uv` used to create and manage the virtual environment.                  |
| `requires-python` |  *(required)*  | Python version constraint for the environment.                                      |
| `requirements`    |      `""`      | Packages to install into the environment.                                           |
| `entrypoint`      |  *(required)*  | Python entrypoint (`module:function`) to execute after the environment is prepared. |

The bootstrap header is interpreted only by `bex`. Once the environment is ready, control is handed off to the configured entrypoint.
