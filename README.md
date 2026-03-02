# bootstrap-spack

A macOS-focused bootstrap tool for [Spack](https://github.com/spack/spack) >= 1.2 with intelligent fork-aware layout and environment management.

## Overview

`bootstrap_spack.py` is a comprehensive tool that automates the setup and configuration of Spack installations on macOS. It handles everything from Homebrew prerequisites to Spack repository cloning, compiler detection, and environment creation with advanced constraint support.

### Key Features

- **Fork-aware layout**: Cleanly separates official Spack from fork installations
  - Official: `~/spack` and `~/spack-packages`
  - Forks: `~/spack-<fork>` and `~/spack-packages-<fork>`
- **Automated environment creation** with compiler and Python version constraints
- **Auto-naming**: Generate environment names from toolchain specs (e.g., `geos-gcc15-py312`)
- **Apple Silicon target auto-resolution**: Chooses a safe default target from host architecture and Apple clang capability
- **Target query modes**: Print resolved target during `env-create` or query it without making changes
- **Sandbox mode**: Isolate installations for testing without affecting your main setup
- **Modular subcommands**: Run individual setup steps or full bootstrap
- **Smart concretizer settings**: Optimizes unification strategy based on constraints

## Prerequisites

- **macOS** (Darwin platform)
- **Homebrew**: The script can help you install it in a non-admin location
- **Git**: For cloning repositories
- **SSH keys**: Set up for GitHub access (`git@github.com`)

## Installation

Simply clone this repository:

```bash
git clone git@github.com:mathomp4/bootstrap-spack.git
cd bootstrap-spack
chmod +x bootstrap_spack.py
```

## Quick Start

### Interactive Full Bootstrap

Run without arguments for an interactive experience:

```bash
./bootstrap_spack.py
```

This will:
1. Prompt you to choose a Spack flavor (official, mathomp4, or custom fork)
2. Install Homebrew prerequisites
3. Clone Spack and related repositories
4. Configure compilers and external packages
5. Create a default `geos` environment with `geosgcm` as the spec

### Non-interactive Full Bootstrap

Skip prompts by specifying the Spack choice:

```bash
# Use official Spack
./bootstrap_spack.py --spack official

# Use mathomp4 fork
./bootstrap_spack.py --spack mathomp4

# Use a custom fork
./bootstrap_spack.py --spack fork --fork jcsda
```

## Subcommands

### `brew`
Install Homebrew prerequisites only (gcc, git, cmake, etc.).

```bash
./bootstrap_spack.py brew
```

### `spack`
Clone the selected Spack repository.

```bash
./bootstrap_spack.py --spack official spack
```

### `repos`
Clone and configure additional repositories (`spack-packages` and `geosesm-spack`).

```bash
./bootstrap_spack.py --spack mathomp4 repos
```

### `config`
Configure Spack settings: build jobs, compiler detection, external package discovery, concretizer settings.

```bash
./bootstrap_spack.py --spack official config
```

### `setup`
Complete setup without creating an environment. Equivalent to: `brew` + `spack` + `repos` + `config`.

```bash
./bootstrap_spack.py --spack mathomp4 setup
```

**This is ideal when you want a configured Spack installation but plan to create environments manually.**

### `env`
Create the default `geos` starter environment with `geosgcm` spec.

```bash
./bootstrap_spack.py --spack mathomp4 env
```

### `env-create`
Create a custom environment with optional compiler and Python constraints. By default, creates an environment that will install dependencies of `geosgcm`. Use `--spec` to specify a different package.

**Target Resolution (Apple Silicon):**
- If you pass `--target`, that value is used (after validation).
- If you omit `--target`, the script auto-selects a conservative target using:
  - host target from `spack arch -t`
  - Apple clang capability cap (current mapping: clang 17→`m3`, clang 16→`m2`, older→`m1`)
- If explicit `--target` exceeds Apple clang capability (e.g., `--target m3` with clang 16), the script exits with a clear error.

**Target Query Flags:**
- `--print-effective-target`: Print resolved target and continue normal `env-create`.
- `--print-effective-target-only`: Print resolved target and exit immediately (no setup/config/environment changes).

**macOS Smart Compiler Defaults:**
On macOS, when you specify `--compiler gcc@X`, the script automatically uses:
- **apple-clang** for C/C++ compilation
- **gcc@X** for Fortran compilation

This is the recommended best practice on macOS for optimal performance and compatibility. Use `--compiler-c` and `--compiler-fortran` for explicit control.

**Important:** apple-clang does not include a Fortran compiler. Using `--compiler apple-clang@17` will result in an error. For Fortran support, use gcc or gfortran.

```bash
# Default environment
./bootstrap_spack.py --spack mathomp4 env-create

# Custom name
./bootstrap_spack.py --spack mathomp4 env-create --name my-project

# With compiler constraint (macOS: apple-clang for C/C++, gcc@15 for Fortran)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --python 3.12
# Creates: geos-gcc15-py312

# With target architecture
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --target x86_64_v3
# Creates: geos-gcc15 optimized for x86_64_v3 microarchitecture

# Print effective target and continue env creation
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 \
  --print-effective-target

# Print effective target only (no environment changes)
./bootstrap_spack.py --spack mathomp4 env-create --print-effective-target-only

# With Python optimizations (PGO for better runtime performance)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --python 3.12 --python-optimizations
# Creates: geos-gcc15-py312 with optimized Python build

# NOTE: --compiler apple-clang@17 alone will ERROR (no Fortran compiler)
# Use one of these instead:

# Explicit compiler control (override macOS smart defaults)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler-c apple-clang@17 --compiler-fortran gcc@15 --python 3.12

# Fortran-only constraint (C/C++ uses default)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler-fortran gcc@15
# Creates: geos-gcc15

# Python constraint only
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --python 3.11
# Creates: geos-py311

# Dependency-only workflow (for developing against geosgcm/mapl/etc) - DEFAULT
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12
# Creates environment for geosgcm dependencies (default spec)
# Then use: spack install --only dependencies

# Use a different spec (e.g., mapl instead of geosgcm)
./bootstrap_spack.py --spack mathomp4 env-create --name mapl-dev \
  --compiler gcc@15 --spec mapl
# Creates environment for building MAPL dependencies

# Specify target architecture for optimized builds
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --target x86_64_v3
# Creates: geos-gcc15-py312 with x86_64_v3 optimizations
```

### `reset`
Backup and remove user-scope configuration files (`repos.yaml`, `packages.yaml`, `concretizer.yaml`).

```bash
./bootstrap_spack.py --spack mathomp4 reset
```

### `config-clean`
Reset configuration, then rebuild repos and config from scratch.

```bash
./bootstrap_spack.py --spack mathomp4 config-clean
```

### `all`
Full bootstrap: `brew` + `spack` + `repos` + `config` + `env`. This is the default if no subcommand is specified.

```bash
./bootstrap_spack.py --spack official all
# or simply
./bootstrap_spack.py --spack official
```

## Sandbox Mode

Sandbox mode allows you to test configurations and installations in an isolated directory without affecting your main Spack setup. This is incredibly useful for:
- Testing script changes
- Experimenting with different configurations
- Debugging environment issues
- Trying out new Spack versions

### How It Works

When you use `--sandbox <directory>`, everything goes into that directory:

```
<sandbox>/
├── spack-mathomp4/              # Spack repository
├── spack-packages-mathomp4/     # spack-packages repository
├── geosesm-spack/               # geosesm-spack repository
├── .spack/                      # User configuration (instead of ~/.spack)
│   ├── config.yaml
│   ├── packages.yaml            # externals + optional packages:all:target
│   ├── repos.yaml
│   └── concretizer.yaml
└── spack-envs/                  # Environments
    └── geos-gcc15-py312/
        └── spack.yaml
```

### Using Sandbox Mode

**Setup a sandboxed installation:**

```bash
./bootstrap_spack.py --sandbox ~/spack-testing --spack official setup
```

**Create an environment in the sandbox:**

```bash
./bootstrap_spack.py --sandbox ~/spack-testing --spack official env-create --auto-name --compiler gcc@15 --python 3.12
```

**Using the sandboxed Spack:**

The script will output instructions like:

```
To enable Spack in this shell:
  export SPACK_USER_CONFIG_PATH="/Users/username/spack-testing/.spack"
  source "/Users/username/spack-testing/spack-official/share/spack/setup-env.sh"
```

Follow these instructions, then use Spack normally:

```bash
export SPACK_USER_CONFIG_PATH="$HOME/spack-testing/.spack"
source "$HOME/spack-testing/spack-official/share/spack/setup-env.sh"
spack env list
spack env activate geos-gcc15-py312
spack concretize
spack install
```

**Cleanup:**

Deactivate any active environment first, then delete the sandbox directory:

```bash
# If you have an environment activated
spack env deactivate

# Then remove the sandbox
rm -rf ~/spack-testing
```

**Important:** Always deactivate Spack environments before removing the sandbox or environment directories to avoid shell state issues.

## Environment Auto-naming

When using `--auto-name` with `env-create`, the environment name is generated from your constraints:

| Compiler | Python | Generated Name |
|----------|--------|----------------|
| `gcc@15` | `3.12` | `geos-gcc15-py312` |
| `gcc@15` | — | `geos-gcc15` |
| — | `3.12` | `geos-py312` |
| `apple-clang@17` | `3.11` | `geos-appleclang17-py311` |
| `nag@7.1` | — | `geos-nag7` |

**Note:** On macOS, when using `--compiler gcc@X`, the script intelligently uses apple-clang for C/C++ and gcc for Fortran, but the environment name reflects the Fortran compiler (gcc@X) since that's the primary differentiator.

## Dependency-Only Development Workflow

**By default, all environments are created with `geosgcm` as the spec**, optimized for the workflow where you install dependencies through Spack and build the main package from your local source. Use `--spec` to specify a different package (e.g., `mapl`).

```bash
# Create environment for developing geosgcm (default)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12

# Or explicitly specify the spec
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --spec geosgcm

# Activate and install dependencies only
export SPACK_USER_CONFIG_PATH="$HOME/.spack"  # or sandbox path
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate geos-gcc15-py312
spack concretize
spack install --only dependencies

# Now build geosgcm from your local source
cd ~/GEOSgcm
make install
```

**The output will tell you which spec is being used:**
```
This environment will install dependencies of: geosgcm
```

**What happens:**
- The environment contains a single spec: `geosgcm` (or your chosen package)
- Compiler constraints are applied via the spec syntax: `%[when='%c'] c=apple-clang %[when='%cxx'] cxx=apple-clang %[when='%fortran'] fortran=gcc@15`
- These constraints propagate to all dependencies automatically
- Compiler environment variables (`CC`, `CXX`, `FC`) are added to work around [Spack bug #51855](https://github.com/spack/spack/issues/51855)
- Running `spack install --only dependencies` installs all dependencies without building the main package

**Common specs:**
- `geosgcm` (default) - GEOSgcm and all dependencies
- `mapl` - MAPL library and dependencies
- `esmf+netcdf` - ESMF with NetCDF support
- Any valid Spack spec

## Compiler Configuration on macOS

On macOS, the recommended practice is to use:
- **apple-clang** for C/C++ (better optimization for Apple Silicon)
- **gcc** for Fortran (better Fortran support)

The script automatically handles this when you specify `--compiler gcc@X`:

```bash
# This command:
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15

# Automatically creates an environment with:
# - apple-clang for C/C++ compilation
# - gcc@15 for Fortran compilation
# - Environment name: geos-gcc15
```

The generated `spack.yaml` will contain:
```yaml
specs:
  - geosgcm %[when='%c'] c=apple-clang %[when='%cxx'] cxx=apple-clang %[when='%fortran'] fortran=gcc@15
```

This tells Spack to:
- Use `apple-clang` for all C compilation
- Use `apple-clang` for all C++ compilation  
- Use `gcc@15` for all Fortran compilation
- Apply these constraints to `geosgcm` and all its dependencies

**Override the smart defaults:**

If you need explicit control over compilers:

```bash
# Explicit apple-clang + gcc split (recommended on macOS)
./bootstrap_spack.py --spack mathomp4 env-create \
  --compiler-c apple-clang@17 --compiler-fortran gcc@15

# Pure gcc for everything (not recommended on macOS, but works)
./bootstrap_spack.py --spack mathomp4 env-create \
  --compiler-c gcc@15 --compiler-fortran gcc@15

# NOTE: This will ERROR because apple-clang has no Fortran compiler:
# ./bootstrap_spack.py --spack mathomp4 env-create --compiler apple-clang@17
```

## Common Workflows

### First-time Setup

```bash
# Interactive - will prompt for Spack choice
./bootstrap_spack.py

# Or non-interactive
./bootstrap_spack.py --spack mathomp4
```

Then activate Spack:

```bash
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env list
spack env activate geos
spack concretize
spack install --only dependencies
```

### Add Environment for Different Compiler

```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler apple-clang@17 --python 3.11
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate geos-appleclang17-py311
spack concretize
spack install --only dependencies
```

### Create Environment with Target Architecture

```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --target x86_64_v3
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate geos-gcc15-py312
spack concretize
spack install --only dependencies
```

### Create Environment with Optimized Python

```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --python-optimizations
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate geos-gcc15-py312
spack concretize
spack install --only dependencies
```

**Note:** The Python build will take significantly longer with `--python-optimizations`, but you'll get 10-30% better Python runtime performance.

### Testing a Fork Before Committing

```bash
# Setup in sandbox
./bootstrap_spack.py --sandbox /tmp/test-spack --spack fork --fork myusername setup

# Test environment creation
./bootstrap_spack.py --sandbox /tmp/test-spack --spack fork --fork myusername env-create --auto-name --compiler gcc@15

# Use it
export SPACK_USER_CONFIG_PATH="/tmp/test-spack/.spack"
source "/tmp/test-spack/spack-myusername/share/spack/setup-env.sh"
spack env activate geos-gcc15
spack concretize

# Clean up when done (deactivate first!)
spack env deactivate
rm -rf /tmp/test-spack
```

### Resetting Configuration

If you need to start fresh with configuration:

```bash
./bootstrap_spack.py --spack mathomp4 config-clean
```

This backs up your old config, removes it, then regenerates everything.

### Developing Against GEOSgcm or MAPL

**Note:** This is now the default behavior. All environments are created with a spec (default: `geosgcm`) optimized for the dependency-only workflow.

```bash
# Create environment for developing geosgcm (default spec)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12

# For a different package, use --spec
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --spec mapl

# Activate and build dependencies
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate geos-gcc15-py312
spack concretize
spack install --only dependencies

# Now your local GEOSgcm build can use these dependencies
cd ~/GEOSgcm
make install
```

Compiler environment variables are automatically added to work around a Spack bug, so your builds will find the correct compilers.

## Dry-run Mode

Preview what the script would do without making any changes:

```bash
./bootstrap_spack.py --dry-run --spack official setup
./bootstrap_spack.py --dry-run --sandbox ~/test --spack mathomp4 all
```

## Python Optimizations

You can build Python with profile-guided optimization (PGO) using the `--python-optimizations` flag. This enables the `+optimizations` variant in Spack, which results in a significantly faster Python interpreter (typically 10-30% performance improvement) at the cost of longer build time.

```bash
# Build Python with optimizations
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --python-optimizations

# Combine with other options
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --python-optimizations --target x86_64_v3
```

**Note:** Building Python with `+optimizations` takes significantly longer (the build compiles Python twice: once to gather profiling data, then again with optimizations based on that profile). The runtime performance improvement is substantial for Python-heavy workloads.

## Target Architecture

You can specify a target microarchitecture for all packages in an environment using the `--target` option. This is useful for:
- Building optimized binaries for specific CPU architectures
- Ensuring compatibility with older systems (e.g., `x86_64_v2`)
- Maximizing performance on newer systems (e.g., `x86_64_v4`, `icelake`)

On Apple Silicon, if `--target` is not provided, the script automatically chooses a conservative default based on host architecture and Apple clang compatibility. This avoids invalid defaults when host hardware is newer than your installed Apple clang.

Target precedence for `packages:all:target` in global `packages.yaml`:
1. Explicit `--target` — always written
2. Apple clang forced downgrade (e.g., clang 16 on M3 host → writes `target: [m2]`)
3. No override needed — nothing written, Spack uses its default

```bash
# Target a specific x86_64 microarchitecture level
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --target x86_64_v3

# Target Intel Icelake optimizations
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --target icelake

# Target Apple M1/M2 (ARM)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --target m1

# Query target only (fast, no setup/env changes)
./bootstrap_spack.py --spack official env-create --print-effective-target-only
```

Common target values:
- `x86_64` - Generic x86_64 (maximum compatibility)
- `x86_64_v2` - Baseline for modern x86_64 (2009+)
- `x86_64_v3` - AVX/AVX2 support (2013+)
- `x86_64_v4` - AVX-512 support (2017+)
- `icelake`, `skylake`, `haswell` - Intel-specific
- `zen`, `zen2`, `zen3` - AMD-specific
- `m1`, `m2`, `m3` - Apple Silicon

The target constraint applies to all packages in the environment, ensuring consistent optimization levels across your entire build.

## Configuration Details

### Build Jobs
Set to `6` parallel jobs by default.

### Environments Root
Set to `~/spack-envs` (or `<sandbox>/spack-envs` in sandbox mode).

### Compiler Detection
Automatically finds all available compilers using `spack compiler find`.

### External Packages
Finds external packages but **excludes**: `bison`, `openssl`, `gmake`, `m4`, `curl`, `python`, `gettext`, `perl`, `meson`.

These are excluded because:
- System versions may be incompatible
- Spack-built versions provide better consistency
- Python is managed via environment constraints

### Concretizer Strategy

The script intelligently sets the `unify` strategy:

- **No constraints** (`unify: true`): Single solve, all packages use same dependencies
- **Compiler only** (`unify: when_possible`): Attempts unified solve, falls back if needed (macOS compatibility)
- **Python or both** (`unify: false`): Independent solves, **much faster** (seconds vs minutes)

## Troubleshooting

### "No environments" when running `spack env list`

Make sure you've set the config path (in sandbox mode):

```bash
export SPACK_USER_CONFIG_PATH="$HOME/spack-testing/.spack"
```

### Concretization takes forever (>5 minutes)

This can happen with complex constraints. The script now uses `unify: false` when Python constraints are specified, which should solve in seconds. If you created an environment with the old settings, recreate it:

```bash
# Deactivate if currently active
spack env deactivate

# Remove and recreate
rm -rf ~/spack-envs/your-env-name
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --python 3.12
```

### SSH key issues when cloning

Make sure you have SSH keys set up for GitHub:

```bash
ssh -T git@github.com
```

If not set up, see [GitHub's SSH key documentation](https://docs.github.com/en/authentication/connecting-to-github-with-ssh).

### Homebrew not found

The script will guide you to install Homebrew in a non-admin location. Follow the instructions in the error message.

### Mixed compilers in concretization output

**This is expected and correct behavior on macOS!**

When you see output like:
```
%c,cxx=apple-clang@17.0.0 %fortran=gcc@15.2.0
```

This means:
- C and C++ are compiled with apple-clang (optimal for macOS)
- Fortran is compiled with gcc (better Fortran support)

This is the recommended configuration and what the script sets up automatically when you use `--compiler gcc@X` on macOS.

### apple-clang error with --compiler

If you see:
```
ERROR: apple-clang does not include a Fortran compiler.
```

This occurs when using `--compiler apple-clang@17` alone. Apple's compiler suite does not include a Fortran compiler. Use one of these alternatives:
- `--compiler gcc@15` (recommended: auto-uses apple-clang for C/C++, gcc for Fortran)
- `--compiler-c apple-clang@17 --compiler-fortran gcc@15` (explicit control)
- `--compiler-fortran gcc@15` (uses default apple-clang for C/C++, gcc for Fortran)

### Compiler environment variables with --spec

When creating environments, the script automatically adds `CC`, `CXX`, and `FC` environment variables to your `spack.yaml` when compiler constraints are specified. This is a workaround for [Spack bug #51855](https://github.com/spack/spack/issues/51855) which affects the `spack install --only dependencies` workflow.

You'll see output like:
```
==> Adding compiler env vars (workaround for Spack bug #51855):
    CC=/usr/bin/clang
    CXX=/usr/bin/clang++
    FC=/opt/homebrew/bin/gfortran-15
```

This ensures your builds use the correct system compilers, not Spack wrapper scripts.

## Advanced Options

### Custom Repository URLs

Override the default repository URLs:

```bash
./bootstrap_spack.py --spack fork --fork myorg \
  --spack-repo myorg/custom-spack \
  --spack-packages-repo myorg/custom-packages \
  setup
```

## Development

Run the script with `--dry-run` to see what would happen without making changes:

```bash
./bootstrap_spack.py --dry-run --spack official all
```

This is useful for debugging or understanding the script's behavior.

## License

See [LICENSE](LICENSE) file.

## Contributing

This is a personal tool but contributions are welcome. Please test changes in sandbox mode before submitting PRs:

```bash
./bootstrap_spack.py --sandbox /tmp/test --dry-run --spack official all
```

## Support

For issues or questions:
- Check the detailed help: `./bootstrap_spack.py -h` or `./bootstrap_spack.py env-create -h`
- Review this README
- Check Spack documentation: https://spack.readthedocs.io/

## Credits

Developed for streamlining Spack setup in the GMAO/GEOS-ESM development environment.
