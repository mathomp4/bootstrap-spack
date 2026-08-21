# bootstrap-spack

A bootstrap tool for [Spack](https://github.com/spack/spack) >= 1.2 with intelligent fork-aware layout and environment management, supporting macOS and Linux.

## Overview

`bootstrap_spack.py` is a comprehensive tool that automates the setup and configuration of Spack installations on macOS and Linux. It handles everything from prerequisite checks to Spack repository cloning, compiler detection with version enforcement, and environment creation with advanced constraint support.

### Key Features

- **Fork-aware layout**: Cleanly separates official Spack from fork installations
  - Official: `~/spack` and `~/spack-packages`
  - Forks: `~/spack-<fork>` and `~/spack-packages-<fork>`
- **Automated environment creation** with compiler and Python version constraints
- **Auto-naming**: Generate environment names from toolchain specs (e.g., `geos-gcc15-py312`)
- **Compiler version enforcement**: Guarantees a known-good compiler is present before proceeding
  - **macOS**: Apple clang >= 17 and Homebrew gfortran >= 14.2 (GCC 14, 15, or 16) required
  - **Linux**: GCC >= 14.2 (GCC 14, 15, or 16) required
- **Auto-compiler selection**: Highest trusted compiler auto-selected for new environments when no `--compiler` is given
- **Apple Silicon target auto-resolution**: Chooses a safe default target from host architecture and Apple clang capability
- **Target query modes**: Print resolved target during `env-create` or query it without making changes
- **Sandbox mode**: Isolate installations for testing without affecting your main setup
- **Modular subcommands**: Run individual setup steps or full bootstrap
- **Smart concretizer settings**: Optimizes unification strategy based on constraints
- **Bundle package workflow**: Default spec is `geosgcm-deps` (a `BundlePackage`), so `spack install` (no `--only dependencies`) is all that is needed; afterwards load everything with `spack load geosgcm-deps`
- **OpenMPI variants pinned**: `+fortran +internal-hwloc +internal-libevent +internal-pmix` are always written into the environment's `spack.yaml`
- **View disabled by default**: Environments are created with `view: false`; pass `--view` to `env-create` to enable a merged filesystem view
- **Compiler env vars always set**: `CC`, `CXX`, and `FC` are always written into `spack.yaml` from Spack's own `packages.yaml` (populated by `spack external find`), not PATH order

## Prerequisites

### macOS
- **Homebrew**: The script can help you install it in a non-admin location
- **Xcode Command Line Tools** with Apple clang >= 17 (`xcode-select --install`)
- **Homebrew gcc** >= 14.2 for Fortran (`brew install gcc@16`)
- **Git**: For cloning repositories
- **SSH keys**: Set up for GitHub access (`git@github.com`)

### Linux
- **GCC** >= 14.2 (including `gfortran`) — GCC 14, 15, or 16
  - Debian/Ubuntu: `sudo apt install gcc-16 gfortran-16`
  - RHEL/Fedora: `sudo dnf install gcc gcc-gfortran`
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
5. Create a default `geos` environment with `geosgcm-deps` as the spec

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
Create the default `geos` starter environment with `geosgcm-deps` as the spec.

```bash
./bootstrap_spack.py --spack mathomp4 env
```

### `env-create`
Create a custom environment with optional compiler and Python constraints. By default, creates an environment using `geosgcm-deps` (a `BundlePackage` containing all GEOSgcm dependencies). Use `--spec` to specify a different package.

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
# Default environment (uses geosgcm-deps)
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

# Enable a merged filesystem view (disabled by default)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --view

# Use a different spec (e.g., geosgcm instead of geosgcm-deps)
./bootstrap_spack.py --spack mathomp4 env-create --name geosgcm-dev \
  --compiler gcc@15 --spec geosgcm

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

# Specify target architecture for optimized builds
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --target x86_64_v3
# Creates: geos-gcc15-py312 with x86_64_v3 optimizations

# Pre-fetch source tarballs during environment creation (useful for offline compute nodes)
./bootstrap_spack.py --spack official env-create --name grads-env \
  --spec 'grads ^hdf5~mpi' --fetch
```

Source fetching uses a 120-second connection timeout and retries failed fetches
up to three times. Override these with `--fetch-timeout` and `--fetch-retries`.
For example, on a slow or intermittently connected login node:

```bash
./bootstrap_spack.py --sandbox /discover/nobackup/projects/gmao/SIteam/Grads-from-Spack \
  --spack official env-create --name grads-env --spec 'grads ^hdf5~mpi' --view --fetch \
  --fetch-timeout 300 --fetch-retries 5
```

### `fetch`
Pre-fetch all source tarballs for an environment's dependencies into Spack's cache. Concretizes the environment and downloads all source archives on internet-connected nodes (e.g., login nodes) prior to building offline on compute nodes.

```bash
# Fetch sources for environment 'geos' (default)
./bootstrap_spack.py --spack official fetch

# Fetch sources for a specific environment by name
./bootstrap_spack.py --spack official fetch --name grads-env

# Fetch sources for an environment by explicit directory path
./bootstrap_spack.py --spack official fetch --env-dir ~/spack-envs/grads-env
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
spack env activate -p geos-gcc15-py312
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

## GEOSgcm Development Workflow

The default spec is `geosgcm-deps`, a Spack `BundlePackage` that declares all of GEOSgcm's dependencies without building GEOSgcm itself. This means:

- `spack install` (no `--only dependencies` needed) builds and installs all dependencies
- `spack load geosgcm-deps` sets up your environment (`PATH`, `CMAKE_PREFIX_PATH`, etc.) in one command
- `CC`, `CXX`, and `FC` are set automatically so CMake picks the right compilers when you build GEOSgcm from source

```bash
# Create environment
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12

# Activate and install
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate -p geos-gcc15-py312
spack concretize
spack install

# Load all dependencies into your shell
spack load geosgcm-deps

# Now build GEOSgcm from your local source
cd ~/GEOSgcm
mepo clone
cmake -B build --install-prefix=$(pwd)/install
cmake --build build --target install -j
```

**The output will tell you what is being installed:**
```
This environment will install: geosgcm-deps
```

**Using a different spec:**

If you need the full `geosgcm` package (e.g., to let Spack build it entirely), use `--spec`:

```bash
./bootstrap_spack.py --spack mathomp4 env-create --name geosgcm-full \
  --compiler gcc@15 --spec geosgcm
# Then: spack install --only dependencies  (geosgcm itself is not built by Spack)
```

Or for MAPL:

```bash
./bootstrap_spack.py --spack mathomp4 env-create --name mapl-dev \
  --compiler gcc@15 --spec mapl
```

## After `spack install` Succeeds

Once `spack install` finishes (all dependencies are built), the workflow is:

### 1. Load the environment

```bash
spack load geosgcm-deps
```

This sets `PATH`, `CMAKE_PREFIX_PATH`, `CC`, `CXX`, `FC`, and all other relevant variables in your current shell so that CMake and compilers are ready to use.

### 2. Clone and build GEOSgcm

```bash
cd ~/GEOSgcm   # (or wherever your checkout lives)
mepo clone
cmake -B build --install-prefix=$(pwd)/install
cmake --build build --target install -j
```

`CC`, `CXX`, and `FC` are set automatically by `spack load` (or by the `envvariables` block written into `spack.yaml` by this script), so CMake picks the correct compilers without any manual export.

### 3. Re-entering the environment in a new shell

Every time you open a new terminal, re-activate before building:

```bash
source ~/spack-mathomp4/share/spack/setup-env.sh   # adjust path for your fork
spack env activate -p geos-gcc15-py312                  # your environment name
spack load geosgcm-deps
```

### Discover Lmod modules

For a Discover-specific GrADS-only Lmod tree, including module hash removal,
module-set configuration, and stale spider-cache troubleshooting, see
[`DISCOVER_LMOD.md`](DISCOVER_LMOD.md).

### Notes

- **`spack install` vs `spack install --only dependencies`**: When the spec is `geosgcm-deps` (a `BundlePackage`, the default), use plain `spack install`. When the spec is `geosgcm` or another non-bundle package, use `spack install --only dependencies` (GEOSgcm itself is built from source via CMake, not by Spack).
- **Rebuilding after source changes**: Just re-run `cmake --build build --target install -j` from the repo root. No need to touch Spack unless you need to add or update a dependency.
- **Updating a dependency**: Run `spack concretize -f && spack install` inside the activated environment, then `spack load geosgcm-deps` again to refresh your shell.

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
  - geosgcm-deps %[when='%c'] c=apple-clang %[when='%cxx'] cxx=apple-clang %[when='%fortran'] fortran=gcc@15
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
spack env activate -p geos
spack concretize
spack install
spack load geosgcm-deps
```

### Add Environment for Different Compiler

```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler apple-clang@17 --python 3.11
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate -p geos-appleclang17-py311
spack concretize
spack install
spack load geosgcm-deps
```

### Create Environment with Target Architecture

```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --target x86_64_v3
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate -p geos-gcc15-py312
spack concretize
spack install
spack load geosgcm-deps
```

### Create Environment with Optimized Python

```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --python-optimizations
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate -p geos-gcc15-py312
spack concretize
spack install
spack load geosgcm-deps
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
spack env activate -p geos-gcc15
spack concretize
spack install
spack load geosgcm-deps

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

```bash
# Create environment for developing geosgcm (default spec: geosgcm-deps)
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12

# For a different package, use --spec
./bootstrap_spack.py --spack mathomp4 env-create --auto-name \
  --compiler gcc@15 --python 3.12 --spec mapl

# Activate and install
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate -p geos-gcc15-py312
spack concretize
spack install

# Load all dependencies into your shell
spack load geosgcm-deps

# Now build GEOSgcm from your local source (CC/CXX/FC are already set)
cd ~/GEOSgcm
mepo clone
cmake -B build --install-prefix=$(pwd)/install
cmake --build build --target install -j
```

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

### Compiler Detection and Version Enforcement

Before running `spack compiler find`, the script checks that a trusted compiler is available:

**macOS:**
- **Apple clang >= 17** must be present (from Xcode / Command Line Tools). If the detected version is older, the script exits with update instructions.
- **Homebrew gcc >= 14.2** (GCC 14, 15, or 16) must be present. If none is found, the script exits with `brew install gcc@16` instructions.
- The highest trusted gfortran version is auto-selected for new environments when no `--compiler` is specified.

**Linux:**
- **GCC >= 14.2** (GCC 14, 15, or 16, with gfortran) must be present. The script scans `PATH` for both plain `gcc` and versioned `gcc-N` binaries. If none qualifies, the script exits with `apt`/`dnf` install instructions.
- The highest trusted GCC version is auto-selected for new environments when no `--compiler` is specified.

Trusted version sets are defined as constants near the top of the script (`MACOS_MIN_APPLE_CLANG_MAJOR`, `MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS`, `LINUX_TRUSTED_GCC_MIN_VERSIONS`) and can be updated as new compiler releases are validated.

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

### Compiler environment variables (CC, CXX, FC)

Every environment created by this script includes `CC`, `CXX`, and `FC` in its `spack.yaml`. These are needed when you build GEOSgcm from source outside Spack — CMake reads them to select the right compilers. Paths are read directly from Spack's `packages.yaml` (written by `spack external find` during `config`/`setup`), so they reflect exactly what Spack detected — not PATH order.

When no `--compiler` flag is given:
- **macOS**: apple-clang for C/C++ and the highest trusted Homebrew gcc for Fortran.
- **Linux**: the highest trusted GCC version for all languages.

### No trusted compiler found (macOS or Linux)

If you see an error like:
```
ERROR: No trusted Homebrew gfortran found on this macOS system.
```
or:
```
ERROR: No trusted GCC found on this Linux system.
```

Install a supported compiler version:
- **macOS**: `brew install gcc@16` (provides gfortran-16 / gcc-16 at 16.1+)
- **Linux (Debian/Ubuntu)**: `sudo apt install gcc-16 gfortran-16`
- **Linux (RHEL/Fedora)**: `sudo dnf install gcc gcc-gfortran`

If you see:
```
ERROR: Apple clang N is too old.
```
Update Xcode / Command Line Tools:
```bash
sudo softwareupdate -i -a
xcode-select --install
```

This is a workaround for [Spack bug #51855](https://github.com/spack/spack/issues/51855).

### OpenMPI variants

For the default GEOS environments, the generated `spack.yaml` includes:

```yaml
packages:
  openmpi:
    require: '+fortran +internal-hwloc +internal-libevent +internal-pmix'
```

These variants are required for a working GEOSgcm build. Custom non-GEOS
specifications, such as `grads`, do not receive this OpenMPI requirement.
If a GEOS environment resolves to a different MPI implementation (e.g.,
`mpich`), the `openmpi:` stanza is silently ignored by Spack.

### Filesystem view

By default environments are created with `view: false`. If you need a merged prefix tree (a single directory with all `lib/`, `include/`, etc. symlinked together), pass `--view` to `env-create`. You can also enable it after the fact by editing `spack.yaml` and re-running `spack concretize && spack install` (which is essentially free since nothing needs to be rebuilt).

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
