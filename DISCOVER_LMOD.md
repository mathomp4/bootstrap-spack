# Discover Lmod Setup

This guide describes a Discover-specific setup for exposing only the Spack-built
GrADS module through Lmod. It keeps the generated module tree separate from the
larger default Spack tree and avoids adding unnecessary dependency modules to
`module avail`.

## Paths

Set these paths to match the sandbox used for the build:

```bash
export SANDBOX=/discover/nobackup/projects/gmao/SIteam/Grads-from-Spack
export SPACK_ROOT="$SANDBOX/spack"
export SPACK_USER_CONFIG_PATH="$SANDBOX/.spack"
export GRADS_LMOD_ROOT="$SANDBOX/spack/share/spack/lmod-grads"
```

For a GrADS-only Spack installation, make this the `default` module set. The
configuration belongs in `$SANDBOX/.spack/modules.yaml`, not in the
environment's `spack.yaml`:

```yaml
modules:
  default:
    roots:
      lmod: /discover/nobackup/projects/gmao/SIteam/Grads-from-Spack/spack/share/spack/lmod-grads
    arch_folder: false
    enable:
      - lmod
    lmod:
      hierarchical: false
      include:
        - grads
      hide_implicits: true
      hash_length: 0
      all:
        autoload: none
```

Update the `roots.lmod` value if `SANDBOX` is different. `hash_length: 0`
removes the concretization hash from the module name, so the module is exposed
as `grads/2.2.3` instead of `grads/2.2.3-gcc-14.2.0-6ohsjn6`.

## Generate the Module

Because the GrADS configuration is the `default` module set, refresh it without
the `-n grads` option:

```bash
"$SPACK_ROOT/bin/spack" module lmod refresh --delete-tree
```

The generated module should be under:

```text
$GRADS_LMOD_ROOT/grads/2.2.3.lua
```

Check the generated files with:

```bash
find "$GRADS_LMOD_ROOT" -type f -name '*.lua' -printf '%P\n' | sort
"$SPACK_ROOT/bin/spack" module lmod find grads --full-path
```

`include: [grads]` limits the generated module set to GrADS. `hide_implicits`
controls what is displayed by `module avail`; it does not reduce the files
that Lmod scans. The isolated `lmod-grads` root is therefore important for
keeping the module search small.

## Use the Module

Start a clean shell, or remove other Spack module roots from `MODULEPATH`:

```bash
module purge
module use "$GRADS_LMOD_ROOT"
module avail
module load grads/2.2.3
```

Do not add the older tree below
`$SPACK_ROOT/share/spack/lmod/linux-sles15-x86_64` unless you also want all of
its compiler and dependency modules.

## Lmod Cache

Discover's older Lmod can encounter a stack overflow when its cached spider
data contains stale or overlapping module paths. If `module avail` fails after
changing `MODULEPATH`, removing the user cache and retrying is effective:

```bash
rm -rf "$HOME/.lmod.d/.cache"
```

Only remove this cache when no other shell or job is regenerating it. Re-run
`module avail` after setting the clean `MODULEPATH` so Lmod rebuilds the cache.
The cache issue is separate from the module hash and from `hide_implicits`.
