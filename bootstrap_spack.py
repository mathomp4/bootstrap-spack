#!/usr/bin/env python3
"""
bootstrap_spack.py

macOS-focused bootstrap for Spack >= 1.2 with fork-aware layout:

- official:  ~/spack            and ~/spack-packages
- fork:      ~/spack-<fork>     and ~/spack-packages-<fork>

SUBCOMMANDS:
  brew          Verify Homebrew installation and install prerequisite packages
  spack         Clone the chosen Spack repository (official or fork)
  repos         Clone spack-packages and geosesm-spack repos, configure repos.yaml
  config        Configure Spack: build_jobs, find compilers/externals, set concretizer
  setup         Complete setup: brew + spack + repos + config (but no environment)
  env           Create default starter environment (geos)
  env-create    Create a custom named/configured environment with compiler/Python pins
  reset         Backup and remove user-scope config files (repos.yaml, packages.yaml, etc.)
  config-clean  Reset config, then rebuild repos and config from scratch
  all           Full bootstrap: brew + spack + repos + config + env (DEFAULT)

DEFAULT BEHAVIOR:
  Running ./bootstrap_spack.py with no arguments is equivalent to:
    ./bootstrap_spack.py all

  This will:
    1. Prompt you to choose which Spack to use (interactive mode)
    2. Install Homebrew prerequisites (macOS only)
    3. Clone the chosen Spack repository
    4. Clone and configure additional repos (spack-packages, geosesm-spack)
    5. Configure Spack (compilers, externals, concretizer settings)
    6. Create a default 'geos' environment

SANDBOX MODE:
  Use --sandbox <dir> to install everything in an isolated directory for testing:
    ./bootstrap_spack.py --sandbox /tmp/test-spack --spack official setup

  This will install:
    - Spack repos:      <sandbox>/spack, <sandbox>/spack-packages, etc.
    - User config:      <sandbox>/.spack (instead of ~/.spack)
    - Environments:     <sandbox>/spack-envs

  Useful for testing changes without affecting your main Spack installation.

USAGE EXAMPLES:

  # Default: full bootstrap with interactive prompts (equivalent to 'all'):
  ./bootstrap_spack.py

  # Full bootstrap with specific Spack choice (skips interactive prompt):
  ./bootstrap_spack.py --spack official all
  ./bootstrap_spack.py --spack mathomp4 all

  # Setup mathomp4 fork without creating an environment:
  ./bootstrap_spack.py --spack mathomp4 setup

  # Setup official Spack:
  ./bootstrap_spack.py --spack official setup

  # Setup a custom fork:
  ./bootstrap_spack.py --spack fork --fork jcsda setup

  # Create default environment (geos) after setup:
  ./bootstrap_spack.py --spack mathomp4 env-create

  # Create environment with auto-generated name based on compiler/Python:
  ./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --python 3.12
  # Result: creates environment named "geos-gcc15-py312"

  # Create environment with custom name and compiler constraint:
  ./bootstrap_spack.py --spack mathomp4 env-create --name my-env --compiler apple-clang@17

    # Print resolved target and continue environment creation:
    ./bootstrap_spack.py --spack mathomp4 env-create --print-effective-target --auto-name --compiler gcc@15

    # Print resolved target only (no setup/env changes):
    ./bootstrap_spack.py --spack mathomp4 env-create --print-effective-target-only

  # Create environment with Python version constraint only:
  ./bootstrap_spack.py --spack mathomp4 env-create --auto-name --python 3.11
  # Result: creates environment named "geos-py311"

  # Clean config and rebuild from scratch:
  ./bootstrap_spack.py --spack mathomp4 config-clean

  # Dry-run mode (see what would happen without making changes):
  ./bootstrap_spack.py --dry-run --spack official all

  # Sandbox mode for testing (isolates everything in one directory):
  ./bootstrap_spack.py --sandbox /tmp/test-spack --spack official setup
  ./bootstrap_spack.py --sandbox ~/spack-testing --spack mathomp4 all

  # Just install Homebrew packages:
  ./bootstrap_spack.py brew

KEY DESIGN NOTES:
- For 'tcsh' external we use `spack python` and Spack's internal config API
  rather than `spack config add` to handle array-of-dicts safely.
- Environments are created under ~/spack-envs (configured via environments_root).
- The --auto-name flag generates environment names from toolchain specs.
- Target precedence is: explicit --target, then auto target on Apple Silicon
    based on min(host target, Apple-clang capability).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import platform
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

BREW_PKGS = [
    "coreutils",
    "gcc",
    "git",
    "wget",
    "bash",
    "tcsh",
    "cmake",
    "openssl",
    "rust",
]

EXTERNAL_FIND_EXCLUDES = [
    "bison",
    "openssl",
    "gmake",
    "m4",
    "curl",
    "python",
    "gettext",
    "perl",
    "meson",
]

# Default spec for environments.
# geosgcm-deps is a BundlePackage that mirrors all of GEOSgcm's dependencies.
# Using it means "spack install" (no --only dependencies flag needed) and
# afterwards users can do "spack load geosgcm-deps" instead of loading each
# dependency package individually.
DEFAULT_SPEC = "geosgcm-deps"


def spack_user_cfg_dir_from_env(sandbox: Path | None = None) -> Path:
    """Return the user config directory path without invoking spack."""
    if sandbox:
        return sandbox / ".spack"
    cfg = os.environ.get("SPACK_USER_CONFIG_PATH")
    if cfg:
        return Path(cfg)
    return Path.home() / ".spack"


def resolve_concrete_compiler_spec(spec: str, packages_yaml: Path | None = None) -> str:
    """
    Resolve a potentially partial compiler spec (e.g., 'gcc@15') to the concrete
    version found in packages.yaml (e.g., 'gcc@15.2.0').

    Spack v1 requires the fortran/c/cxx compiler references in specs to be
    either external (concrete) or fully versioned. A partial version like 'gcc@15'
    is treated as a version range and fails concretization with:
      "Only external, or concrete, compilers are allowed for the fortran language"

    If no match is found, returns the original spec unchanged.
    """
    if not spec or "@" not in spec:
        return spec

    name, ver = spec.split("@", 1)
    # Already fully concrete (has at least two dots, e.g., 15.2.0)
    if ver.count(".") >= 2:
        return spec

    # Try to find a matching concrete version in packages.yaml
    if packages_yaml is None or not packages_yaml.exists():
        return spec

    try:
        import re as _re

        text = packages_yaml.read_text()
        # Look for lines like: - spec: gcc@15.2.0 ...
        pattern = _re.compile(r"spec:\s*" + _re.escape(name) + r"@(\d+\.\d+\.\d+[^\s]*)")
        # Find all matches, pick the one whose major version matches
        ver_major = ver.split(".")[0]
        candidates = []
        for m in pattern.finditer(text):
            full_ver = m.group(1)
            if full_ver.startswith(ver_major + "."):
                candidates.append(full_ver)
        if len(candidates) == 1:
            resolved = f"{name}@{candidates[0]}"
            eprint(f"==> Resolved compiler spec: {spec} -> {resolved}")
            return resolved
        elif len(candidates) > 1:
            # Multiple matches — pick the highest
            candidates.sort(key=lambda v: [int(x) for x in v.split(".")[:3] if x.isdigit()])
            resolved = f"{name}@{candidates[-1]}"
            eprint(
                f"==> Resolved compiler spec (multiple matches, picked highest): {spec} -> {resolved}"
            )
            return resolved
    except Exception:
        pass

    return spec


def _parse_packages_yaml_compilers(packages_yaml: Path) -> dict[str, list[dict]]:
    """
    Parse packages.yaml and return a dict mapping compiler package name
    (e.g. 'gcc', 'apple-clang') to a list of dicts with keys:
      version (tuple of ints), c, cxx, fortran (paths, may be None)
    Entries without extra_attributes.compilers are skipped.
    """
    import re as _re

    result: dict[str, list[dict]] = {}
    if not packages_yaml.exists():
        return result

    try:
        text = packages_yaml.read_text()
    except OSError:
        return result

    # Minimal YAML parse: find each compiler package block and its externals.
    # We use regex rather than a full YAML parser to avoid a dependency on PyYAML
    # (Spack ships its own ruamel; we don't want to import it from outside Spack).
    #
    # Structure we're looking for:
    #   <pkg_name>:
    #     externals:
    #     - spec: gcc@15.2.0 ...
    #       ...
    #       extra_attributes:
    #         compilers:
    #           c: /path
    #           cxx: /path
    #           fortran: /path

    # Package names sit at 2-space indent under the top-level "packages:" key.
    pkg_block_re = _re.compile(r"^  (\S[^:\n]+):\s*$", _re.MULTILINE)
    positions = [(m.group(1).strip(), m.start()) for m in pkg_block_re.finditer(text)]
    positions.append(("__end__", len(text)))

    for i, (pkg_name, start) in enumerate(positions[:-1]):
        if pkg_name not in ("gcc", "apple-clang", "clang", "intel", "aocc"):
            continue
        block = text[start : positions[i + 1][1]]

        # Find each external entry's spec
        spec_re = _re.compile(r"-\s+spec:\s+(\S+)")
        # Find compiler paths within an extra_attributes.compilers block.
        # Anchor to start-of-line + whitespace to avoid matching "spec: apple-clang..."
        # where "spec:" ends with the letters "c:".
        compiler_c_re = _re.compile(r"^\s+c:\s+(\S+)", _re.MULTILINE)
        compiler_cxx_re = _re.compile(r"^\s+cxx:\s+(\S+)", _re.MULTILINE)
        compiler_fc_re = _re.compile(r"^\s+fortran:\s+(\S+)", _re.MULTILINE)

        # Split block into per-entry chunks (each starts with "    - spec:")
        entry_re = _re.compile(r"^\s+-\s+spec:", _re.MULTILINE)
        entry_positions = [m.start() for m in entry_re.finditer(block)]
        entry_positions.append(len(block))

        entries = result.setdefault(pkg_name, [])
        for j in range(len(entry_positions) - 1):
            chunk = block[entry_positions[j] : entry_positions[j + 1]]
            spec_m = spec_re.search(chunk)
            if not spec_m:
                continue
            spec_str = spec_m.group(1)
            # Extract version from spec (e.g. gcc@15.2.0)
            ver_m = _re.search(r"@([\d.]+)", spec_str)
            if not ver_m:
                continue
            ver_str = ver_m.group(1)
            try:
                ver_tuple = tuple(int(x) for x in ver_str.split(".") if x.isdigit())
            except ValueError:
                continue

            c_path = (
                compiler_c_re.search(chunk) or type("", (), {"group": lambda s, n: None})()
            ).group(1)
            cxx_path = (
                compiler_cxx_re.search(chunk) or type("", (), {"group": lambda s, n: None})()
            ).group(1)
            fc_path = (
                compiler_fc_re.search(chunk) or type("", (), {"group": lambda s, n: None})()
            ).group(1)

            if not (c_path or cxx_path or fc_path):
                continue

            entries.append(
                {
                    "version": ver_tuple,
                    "spec": spec_str,
                    "c": c_path,
                    "cxx": cxx_path,
                    "fortran": fc_path,
                }
            )

    return result


def find_compiler_paths_from_packages_yaml(
    packages_yaml: Path,
    c_spec: str | None,
    fortran_spec: str | None,
) -> dict[str, str]:
    """
    Resolve CC, CXX, FC from packages.yaml using Spack's recorded compiler paths.

    When c_spec/fortran_spec are given, match by package name + major version.
    When they are None (no explicit compiler), pick the highest available version
    of apple-clang (CC/CXX) and gcc (FC) — matching the macOS default strategy.
    """
    parsed = _parse_packages_yaml_compilers(packages_yaml)
    env_vars: dict[str, str] = {}

    def best_entry(pkg: str, major: str | None) -> dict | None:
        entries = parsed.get(pkg, [])
        if not entries:
            return None
        if major is not None:
            entries = [e for e in entries if e["version"] and str(e["version"][0]) == major]
        if not entries:
            return None
        return max(entries, key=lambda e: e["version"])

    # --- C / CXX ---
    if c_spec:
        if "apple-clang" in c_spec or "clang" in c_spec:
            pkg = "apple-clang" if "apple-clang" in c_spec else "clang"
            major = c_spec.split("@")[1].split(".")[0] if "@" in c_spec else None
            entry = best_entry(pkg, major)
        elif "gcc" in c_spec:
            major = c_spec.split("@")[1].split(".")[0] if "@" in c_spec else None
            entry = best_entry("gcc", major)
        else:
            entry = None
        if entry:
            if entry.get("c"):
                env_vars["CC"] = entry["c"]
            if entry.get("cxx"):
                env_vars["CXX"] = entry["cxx"]
    else:
        # No explicit c_spec: prefer apple-clang, fall back to highest gcc
        entry = (
            best_entry("apple-clang", None) or best_entry("clang", None) or best_entry("gcc", None)
        )
        if entry:
            if entry.get("c"):
                env_vars["CC"] = entry["c"]
            if entry.get("cxx"):
                env_vars["CXX"] = entry["cxx"]

    # --- FC ---
    if fortran_spec:
        if "gcc" in fortran_spec or "gfortran" in fortran_spec:
            major = fortran_spec.split("@")[1].split(".")[0] if "@" in fortran_spec else None
            entry = best_entry("gcc", major)
        else:
            entry = None
        if entry and entry.get("fortran"):
            env_vars["FC"] = entry["fortran"]
    else:
        # No explicit fortran_spec: pick highest gcc with a fortran path
        gcc_entries = [e for e in parsed.get("gcc", []) if e.get("fortran")]
        if gcc_entries:
            entry = max(gcc_entries, key=lambda e: e["version"])
            env_vars["FC"] = entry["fortran"]

    return env_vars


# Trusted GCC versions on Linux: any patch release where major.minor >= the
# listed minimum.  E.g. (14, 2) accepts 14.2.x, 14.3.x, … but not 14.1.x.
LINUX_TRUSTED_GCC_MIN_VERSIONS: dict[int, int] = {14: 2, 15: 2}

# Minimum Apple clang major version required on macOS.
MACOS_MIN_APPLE_CLANG_MAJOR: int = 17

# Trusted Homebrew gfortran / gcc versions on macOS: same rule as Linux —
# any patch release where major.minor >= the listed minimum.
MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS: dict[int, int] = {14: 2, 15: 2}


def _gcc_full_version(gcc_bin: str) -> tuple[int, int, int] | None:
    """
    Return the (major, minor, patch) version tuple for a GCC binary,
    or None if the binary cannot be queried / version cannot be parsed.
    """
    try:
        r = subprocess.run(
            [gcc_bin, "--version"],
            check=False,
            text=True,
            capture_output=True,
        )
        if r.returncode != 0:
            return None
        # First line is something like:
        #   gcc (GCC) 15.2.0
        #   gcc-15 (Ubuntu 15.2.0-1ubuntu1) 15.2.0
        # We want the last "X.Y.Z" on the first line.
        first_line = r.stdout.splitlines()[0] if r.stdout else ""
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", first_line)
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def _is_trusted_gcc(ver: tuple[int, int, int], trusted: dict[int, int]) -> bool:
    """Return True when ver's major is in *trusted* and minor >= the minimum for that major."""
    min_minor = trusted.get(ver[0])
    return min_minor is not None and ver[1] >= min_minor


def detect_linux_gcc_versions() -> list[tuple[tuple[int, int, int], str]]:
    """
    Scan PATH for GCC binaries on Linux and return all trusted versions.

    Probes both versioned names (gcc-14, gcc-15, …) and the plain 'gcc'
    binary (for distros that ship a single compiler without numbered symlinks).

    A version is "trusted" when its major is in LINUX_TRUSTED_GCC_MIN_VERSIONS
    and its minor >= the minimum for that major (e.g. >= 14.2, >= 15.2).

    Returns a list of ((major, minor, patch), gcc_binary_path) tuples,
    sorted ascending by version, with duplicates removed.
    """
    candidates: dict[tuple[int, int, int], str] = {}  # version -> first-found path

    for p in os.environ.get("PATH", "").split(os.pathsep):
        d = Path(p)
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            # Match both plain "gcc" and versioned "gcc-N"
            if not (entry.name == "gcc" or re.fullmatch(r"gcc-\d+", entry.name)):
                continue
            if not os.access(entry, os.X_OK):
                continue
            ver = _gcc_full_version(str(entry))
            if ver is None:
                continue
            if not _is_trusted_gcc(ver, LINUX_TRUSTED_GCC_MIN_VERSIONS):
                continue
            # Keep the first PATH occurrence for each version tuple
            if ver not in candidates:
                candidates[ver] = str(entry)

    return sorted(candidates.items(), key=lambda t: t[0])


def require_linux_gcc(*, dry_run: bool) -> str:
    """
    Ensure at least one trusted GCC (see LINUX_TRUSTED_GCC_MIN_VERSIONS) is
    available on Linux.  Returns a Spack compiler spec string for the highest
    qualifying version (e.g. 'gcc@15.3.0').
    Raises SystemExit if none is found (unless dry_run).
    """
    if dry_run:
        trusted_str = ", ".join(
            f">= {maj}.{mn}" for maj, mn in sorted(LINUX_TRUSTED_GCC_MIN_VERSIONS.items())
        )
        eprint(f"[dry-run] would check for trusted GCC ({trusted_str}) on Linux")
        return "gcc@14.2.0"

    versions = detect_linux_gcc_versions()
    if not versions:
        example_major = max(LINUX_TRUSTED_GCC_MIN_VERSIONS)
        example_minor = LINUX_TRUSTED_GCC_MIN_VERSIONS[example_major]
        trusted_str = ", ".join(
            f">= {maj}.{mn}" for maj, mn in sorted(LINUX_TRUSTED_GCC_MIN_VERSIONS.items())
        )
        raise SystemExit(
            f"ERROR: No trusted GCC found on this Linux system.\n"
            f"       Trusted versions: {trusted_str}\n"
            f"       e.g.:  sudo apt install gcc-{example_major} gfortran-{example_major}  # Debian/Ubuntu\n"
            f"              sudo dnf install gcc-{example_major} gcc-gfortran              # RHEL/Fedora\n"
            f"\n"
            f"       If you have GCC installed as plain 'gcc', ensure 'gcc --version'\n"
            f"       reports a trusted version (e.g., {example_major}.{example_minor}+)."
        )

    highest_ver, highest_path = versions[-1]
    ver_str = ".".join(str(x) for x in highest_ver)
    eprint(
        "==> Linux GCC check: found trusted GCC versions: "
        + ", ".join(".".join(str(x) for x in v) for v, _ in versions)
    )
    eprint(f"==> Selected: gcc@{ver_str} ({highest_path})")
    return f"gcc@{ver_str}"


def _detect_macos_apple_clang_major() -> int | None:
    """
    Return the Apple clang major version by running ``clang --version``,
    or None if clang is not found or the version cannot be parsed.
    """
    clang = shutil_which("clang")
    if not clang:
        return None
    try:
        r = subprocess.run(
            [clang, "--version"],
            check=False,
            text=True,
            capture_output=True,
        )
        if r.returncode != 0:
            return None
        m = re.search(r"Apple clang version\s+(\d+)", r.stdout)
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None


def _detect_macos_gfortran_versions() -> list[tuple[tuple[int, int, int], str]]:
    """
    Scan PATH for Homebrew gfortran / gcc Fortran compilers on macOS.

    Probes ``gfortran``, ``gfortran-N``, ``gcc-N`` (gcc also ships gfortran
    on Homebrew) using ``--version``.  Filters against
    MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS and returns a sorted ascending list of
    ``((major, minor, patch), binary_path)`` tuples with duplicates removed.
    """
    candidates: dict[tuple[int, int, int], str] = {}

    for p in os.environ.get("PATH", "").split(os.pathsep):
        d = Path(p)
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            if not (
                entry.name == "gfortran"
                or re.fullmatch(r"gfortran-\d+", entry.name)
                or re.fullmatch(r"gcc-\d+", entry.name)
            ):
                continue
            if not os.access(entry, os.X_OK):
                continue
            ver = _gcc_full_version(str(entry))
            if ver is None:
                continue
            if not _is_trusted_gcc(ver, MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS):
                continue
            if ver not in candidates:
                candidates[ver] = str(entry)

    return sorted(candidates.items(), key=lambda t: t[0])


def require_macos_compilers(*, dry_run: bool) -> str:
    """
    On macOS, verify:
      1. Apple clang >= MACOS_MIN_APPLE_CLANG_MAJOR (currently 17) is available.
      2. At least one trusted Homebrew gfortran/gcc is available
         (MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS: major 14 >= 14.2, major 15 >= 15.2).

    Returns a Spack compiler spec string for the highest qualifying gfortran
    version (e.g. ``'gcc@15.3.0'``), which callers use to auto-select the
    Fortran compiler for environment creation.

    Raises SystemExit with actionable instructions if either check fails,
    unless *dry_run* is True (in which case it returns a placeholder spec).
    """
    if dry_run:
        trusted_str = ", ".join(
            f">= {maj}.{mn}" for maj, mn in sorted(MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS.items())
        )
        eprint(
            f"[dry-run] would check Apple clang >= {MACOS_MIN_APPLE_CLANG_MAJOR}"
            f" and trusted gfortran ({trusted_str}) on macOS"
        )
        return "gcc@14.2.0"

    # --- Apple clang check ---
    clang_major = _detect_macos_apple_clang_major()
    if clang_major is None:
        raise SystemExit(
            "ERROR: Could not detect Apple clang on this macOS system.\n"
            "       Please install Xcode Command Line Tools:\n"
            "         xcode-select --install\n"
            "       Then re-run this script."
        )
    if clang_major < MACOS_MIN_APPLE_CLANG_MAJOR:
        raise SystemExit(
            f"ERROR: Apple clang {clang_major} is too old.\n"
            f"       Minimum required: Apple clang {MACOS_MIN_APPLE_CLANG_MAJOR}.\n"
            f"       Please update Xcode / Command Line Tools:\n"
            f"         sudo softwareupdate -i -a\n"
            f"         xcode-select --install"
        )
    eprint(
        f"==> macOS Apple clang check: found clang {clang_major} (>= {MACOS_MIN_APPLE_CLANG_MAJOR} OK)"
    )

    # --- Homebrew gfortran check ---
    gfort_versions = _detect_macos_gfortran_versions()
    if not gfort_versions:
        example_major = max(MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS)
        trusted_str = ", ".join(
            f">= {maj}.{mn}" for maj, mn in sorted(MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS.items())
        )
        raise SystemExit(
            f"ERROR: No trusted Homebrew gfortran found on this macOS system.\n"
            f"       Trusted versions: {trusted_str}\n"
            f"       Install with Homebrew:\n"
            f"         brew install gcc@{example_major}\n"
            f"       Then re-run this script."
        )

    highest_ver, highest_path = gfort_versions[-1]
    ver_str = ".".join(str(x) for x in highest_ver)
    eprint(
        "==> macOS gfortran check: found trusted versions: "
        + ", ".join(".".join(str(x) for x in v) for v, _ in gfort_versions)
    )
    eprint(f"==> Selected: gcc@{ver_str} ({highest_path})")
    return f"gcc@{ver_str}"


def find_system_compiler_paths(c_spec: str | None, fortran_spec: str | None) -> dict[str, str]:
    """
    Find actual system paths to compilers (not Spack wrappers).
    Returns dict with CC, CXX, and/or FC set to system paths.
    Falls back to PATH probing when no packages.yaml is available.
    """
    env_vars = {}

    # C/C++ compiler
    if c_spec and "gcc" in c_spec:
        # gcc-based C/C++
        # Extract version if specified (gcc@15 -> gcc-15)
        if "@" in c_spec:
            name, ver = c_spec.split("@", 1)
            ver_major = ver.split(".")[0]
            gcc_name = f"gcc-{ver_major}"
            gxx_name = f"g++-{ver_major}"
        else:
            gcc_name = "gcc"
            gxx_name = "g++"

        gcc_path = shutil_which(gcc_name)
        gxx_path = shutil_which(gxx_name)
        if gcc_path:
            env_vars["CC"] = gcc_path
        if gxx_path:
            env_vars["CXX"] = gxx_path
    else:
        # Default to clang/clang++ (apple-clang or system default)
        clang_path = shutil_which("clang")
        clangxx_path = shutil_which("clang++")
        if clang_path:
            env_vars["CC"] = clang_path
        if clangxx_path:
            env_vars["CXX"] = clangxx_path

    # Fortran compiler
    if fortran_spec:
        if "gcc" in fortran_spec or "gfortran" in fortran_spec:
            # Extract version for gfortran
            if "@" in fortran_spec:
                _, ver = fortran_spec.split("@", 1)
                ver_major = ver.split(".")[0]
                gfortran_name = f"gfortran-{ver_major}"
            else:
                gfortran_name = "gfortran"

            gfortran_path = shutil_which(gfortran_name)
            if gfortran_path:
                env_vars["FC"] = gfortran_path
    return env_vars


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _spec_is_bundle(spec: str | None) -> bool:
    """Return True when spec looks like a BundlePackage (currently geosgcm-deps)."""
    if not spec:
        return False
    # Normalise: strip variants / compiler suffixes so we only check the name.
    base = spec.split()[0].split("%")[0].split("@")[0].strip()
    return base == "geosgcm-deps"


def print_minimal_advice(
    spack_root: str,
    env_name: str | None = None,
    sandbox: Path | None = None,
    spec: str | None = None,
) -> None:
    eprint("")
    eprint("=" * 64)
    eprint("Spack bootstrap complete.")
    eprint("")
    eprint("To enable Spack in this shell:")
    if sandbox:
        eprint(f'  export SPACK_USER_CONFIG_PATH="{sandbox / ".spack"}"')
    eprint(f'  source "{spack_root}/share/spack/setup-env.sh"')
    eprint("")
    if env_name:
        if spec:
            eprint(f"This environment will install: {spec}")
            eprint("")
        eprint("Next steps:")
        eprint("  spack env list")
        eprint(f"  spack env activate -p {env_name}")
        eprint("  spack concretize")
        if _spec_is_bundle(spec):
            # BundlePackages have no build phase; plain 'spack install' is correct.
            eprint("  spack install")
            eprint("")
            eprint("After installation, load all GEOSgcm dependencies with:")
            eprint(f"  spack load {spec.split()[0] if spec else 'geosgcm-deps'}")
            eprint("")
            eprint("Then build GEOSgcm from your local source tree:")
            eprint("  cd ~/GEOSgcm   # (or wherever your checkout lives)")
            eprint("  mepo clone")
            eprint("  cmake -B build")
            eprint("  cmake --build build --target install -j")
        else:
            eprint("  spack install --only dependencies")
        eprint("")
    eprint("=" * 64)
    eprint("")


platforms = {
    "MacOS": "darwin",
    "Linux": "linux",
}


def is_supported_platform() -> bool:
    return sys.platform in list(platforms.values())


def is_mac_os() -> bool:
    return sys.platform == platforms["MacOS"]


def is_linux() -> bool:
    return sys.platform == platforms["Linux"]


def parse_apple_silicon_target_generation(target: str | None) -> int | None:
    """Parse Spack Apple Silicon targets like m1, m2, m3 -> 1, 2, 3."""
    if not target:
        return None
    match = re.fullmatch(r"m(\d+)", target.strip().lower())
    if not match:
        return None
    return int(match.group(1))


def apple_clang_max_target(clang_major: int) -> str:
    """Conservative Apple-clang target cap for Apple Silicon generations."""
    if clang_major >= 17:
        return "m3"
    if clang_major == 16:
        return "m2"
    return "m1"


def detect_apple_clang_major(*, dry_run: bool) -> int | None:
    if dry_run:
        return None
    clang = shutil_which("clang")
    if not clang:
        return None
    res = run([clang, "--version"], dry_run=False, check=False)
    if res.returncode != 0:
        return None
    match = re.search(r"Apple clang version\s+(\d+)", res.stdout)
    if not match:
        return None
    return int(match.group(1))


def detect_spack_host_target(
    spack_root: str, *, dry_run: bool, sandbox: Path | None = None
) -> str | None:
    if dry_run:
        return None

    # Prefer spack from selected spack_root if it exists.
    setup_env = Path(spack_root) / "share" / "spack" / "setup-env.sh"
    if setup_env.exists():
        res = spack_run(spack_root, ["arch", "-t"], dry_run=False, check=False, sandbox=sandbox)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if lines:
                return lines[-1]

    # Fallback to a spack command already available in PATH.
    if have("spack"):
        res = run(["spack", "arch", "-t"], dry_run=False, check=False)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if lines:
                return lines[-1]

    # Last fallback on Apple Silicon if spack isn't available yet.
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "m1"

    return None


def resolve_effective_target(
    spack_root: str,
    requested_target: str | None,
    *,
    dry_run: bool,
    sandbox: Path | None = None,
) -> str | None:
    """
    Resolve target precedence:
      1) explicit --target
      2) auto target from min(host_target, apple-clang cap) on Apple Silicon
      3) None (let Spack default)

    Always returns the resolved target (or None if not on Apple Silicon / not
    determinable). Use resolve_packages_all_target() when you only want the
    target if it actually needs to be written to config (i.e. explicit or
    forced downgrade).
    """
    if not is_mac_os():
        return requested_target

    host_target = detect_spack_host_target(spack_root, dry_run=dry_run, sandbox=sandbox)
    clang_major = detect_apple_clang_major(dry_run=dry_run)

    if requested_target:
        req_gen = parse_apple_silicon_target_generation(requested_target)
        if clang_major is not None and req_gen is not None:
            cap_target = apple_clang_max_target(clang_major)
            cap_gen = parse_apple_silicon_target_generation(cap_target)
            if cap_gen is not None and req_gen > cap_gen:
                raise SystemExit(
                    f"ERROR: requested --target {requested_target} exceeds Apple clang {clang_major} capability ({cap_target})."
                )
        return requested_target

    host_gen = parse_apple_silicon_target_generation(host_target)
    if clang_major is None or host_gen is None:
        return None

    cap_target = apple_clang_max_target(clang_major)
    cap_gen = parse_apple_silicon_target_generation(cap_target)
    if cap_gen is None:
        return None

    selected_target = f"m{min(host_gen, cap_gen)}"
    if selected_target != host_target:
        eprint(
            f"==> Auto target: host={host_target}, Apple clang={clang_major} -> using target={selected_target}"
        )
    else:
        eprint(
            f"==> Auto target: host={host_target}, Apple clang={clang_major} -> using host target"
        )
    return selected_target


def resolve_packages_all_target(
    spack_root: str,
    requested_target: str | None,
    *,
    dry_run: bool,
    sandbox: Path | None = None,
) -> str | None:
    """
    Return a target to write to packages:all:target only when necessary:
      - explicit --target was passed, OR
      - Apple clang capability forced a downgrade below the host target.

    Returns None when the host target is already compatible (no override needed),
    so we don't pollute packages.yaml with a redundant constraint.
    """
    if not is_mac_os():
        return requested_target

    # Explicit request: always honour it (resolve_effective_target validates it)
    if requested_target:
        return resolve_effective_target(
            spack_root, requested_target, dry_run=dry_run, sandbox=sandbox
        )

    host_target = detect_spack_host_target(spack_root, dry_run=dry_run, sandbox=sandbox)
    clang_major = detect_apple_clang_major(dry_run=dry_run)

    host_gen = parse_apple_silicon_target_generation(host_target)
    if clang_major is None or host_gen is None:
        return None

    cap_target = apple_clang_max_target(clang_major)
    cap_gen = parse_apple_silicon_target_generation(cap_target)
    if cap_gen is None:
        return None

    if cap_gen < host_gen:
        # Downgrade required — write target to config
        selected_target = f"m{cap_gen}"
        eprint(
            f"==> Target downgrade required: host={host_target}, Apple clang={clang_major} cap={cap_target} -> writing target={selected_target} to packages.yaml"
        )
        return selected_target

    # No downgrade needed — let Spack use its default
    return None


def run(
    cmd: Sequence[str], *, dry_run: bool, check: bool = True, env: dict | None = None
) -> subprocess.CompletedProcess:
    if dry_run:
        eprint("[dry-run]", " ".join(shlex.quote(c) for c in cmd))
        # Fake a CompletedProcess
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    # Merge custom env with current environment
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(cmd, check=check, text=True, capture_output=True, env=run_env)


def run_bash(script: str, *, dry_run: bool, check: bool = True) -> subprocess.CompletedProcess:
    return run(["bash", "-lc", script], dry_run=dry_run, check=check)


def have(cmd: str) -> bool:
    return shutil_which(cmd) is not None


def shutil_which(cmd: str) -> str | None:
    # Avoid importing shutil just for which; keep minimal.
    for p in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(p) / cmd
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def ensure_homebrew(*, dry_run: bool) -> str:
    brew = shutil_which("brew")
    if brew:
        eprint(f"==> Homebrew detected: {brew}")
        return brew

    # Common non-admin location
    alt = Path.home() / ".homebrew" / "bin" / "brew"
    if alt.exists():
        eprint(f"==> Homebrew detected: {alt}")
        return str(alt)

    msg = """
Homebrew not found.

Install it (non-admin clone approach):
  mkdir -p "$HOME/.homebrew"
  git clone https://github.com/Homebrew/brew "$HOME/.homebrew"
  eval "$("$HOME/.homebrew/bin/brew" shellenv)"
  brew update --force --quiet
  chmod -R go-w "$(brew --prefix)/share/zsh"

Then re-run:
  ./bootstrap_spack.py brew
"""
    raise SystemExit(msg.strip() + "\n")


def ensure_brew_prereqs(brew: str, *, dry_run: bool) -> None:
    eprint("==> Ensuring Homebrew prerequisites (no modulefiles)...")
    for pkg in BREW_PKGS:
        if dry_run:
            eprint(f"[dry-run] would check/install brew package: {pkg}")
            continue
        r = run([brew, "list", "--formula", pkg], dry_run=False, check=False)
        if r.returncode == 0:
            eprint(f"==> brew: {pkg} already installed")
        else:
            eprint(f"==> brew: installing {pkg}")
            run([brew, "install", pkg], dry_run=False)


def git_clone_if_missing(url: str, dest: Path, *, dry_run: bool) -> None:
    if (dest / ".git").exists():
        eprint(f"==> Repo already cloned: {dest}")
        return
    eprint(f"==> Cloning {url} -> {dest}")
    if dry_run:
        eprint(f"[dry-run] would mkdir {dest.parent}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["git", "clone", "-c", "feature.manyFiles=true", url, str(dest)],
        dry_run=dry_run,
    )


def spack_layout(
    spack_choice: str,
    fork: str | None,
    spack_repo: str | None,
    spack_packages_repo: str | None,
    sandbox: Path | None = None,
) -> dict:
    """
    Return dict with:
      fork_slug, spack_repo, spack_packages_repo, spack_root, spack_packages_dir

    If sandbox is provided, all paths will be under sandbox directory.
    """
    base = sandbox if sandbox else Path.home()

    if spack_choice == "official":
        return dict(
            fork_slug="official",
            spack_repo="spack/spack",
            spack_packages_repo="spack/spack-packages",
            spack_root=str(base / "spack"),
            spack_packages_dir=str(base / "spack-packages"),
        )

    if spack_choice == "mathomp4":
        fork_slug = "mathomp4"
        return dict(
            fork_slug=fork_slug,
            spack_repo="mathomp4/spack",
            spack_packages_repo="mathomp4/spack-packages",
            spack_root=str(base / f"spack-{fork_slug}"),
            spack_packages_dir=str(base / f"spack-packages-{fork_slug}"),
        )

    # fork
    if not fork:
        raise SystemExit("ERROR: --fork is required when --spack fork")
    fork_slug = slugify(fork)
    return dict(
        fork_slug=fork_slug,
        spack_repo=spack_repo or f"{fork_slug}/spack",
        spack_packages_repo=spack_packages_repo or f"{fork_slug}/spack-packages",
        spack_root=str(base / f"spack-{fork_slug}"),
        spack_packages_dir=str(base / f"spack-packages-{fork_slug}"),
    )


def slugify(s: str) -> str:
    s = s.strip().lower()
    out = []
    for ch in s:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out).strip("-")
    if not slug:
        raise SystemExit("ERROR: Could not derive fork slug.")
    return slug


def pick_spack_interactive() -> tuple[str, str | None]:
    # Returns (choice, forkname_if_any)
    eprint("Which Spack do you want to use?")
    eprint("  [1] official (spack/spack)")
    eprint("  [2] mathomp4 fork (mathomp4/spack)")
    eprint("  [3] another fork")
    ans = input("Enter choice [1-3]: ").strip()
    if ans == "1":
        return ("official", None)
    if ans == "2":
        return ("mathomp4", None)
    if ans == "3":
        fk = input("Enter fork org/user name (e.g., jcsda, mylab, acme): ").strip()
        return ("fork", fk)
    raise SystemExit("ERROR: invalid selection")


def make_spack_env(sandbox: Path | None = None) -> dict[str, str]:
    """Create environment dict for running spack commands."""
    env = {}
    if sandbox:
        env["SPACK_USER_CONFIG_PATH"] = str(sandbox / ".spack")
    return env


def spack_bash_prefix(spack_root: str, env_vars: dict | None = None) -> str:
    # Using bash -lc so we can source setup-env.sh.
    # Unset SPACK_ENV and SPACK_CONCRETE_ENV_DIR as a defensive measure — main()
    # already rejects runs where SPACK_ENV is set, but unset here guards against
    # any future code paths that invoke subprocesses without going through main().
    prefix = "unset SPACK_ENV SPACK_CONCRETE_ENV_DIR && "
    if env_vars:
        for key, val in env_vars.items():
            prefix += f"export {key}={shlex.quote(str(val))} && "
    return f"{prefix}source {shlex.quote(spack_root)}/share/spack/setup-env.sh"


def spack_cmd(spack_root: str, args: Sequence[str], env_vars: dict | None = None) -> str:
    # Return a bash -lc string that runs spack with given args.
    return f"{spack_bash_prefix(spack_root, env_vars)} && spack {' '.join(shlex.quote(a) for a in args)}"


def spack_run(
    spack_root: str,
    args: Sequence[str],
    *,
    dry_run: bool,
    check: bool = True,
    sandbox: Path | None = None,
) -> subprocess.CompletedProcess:
    env_vars = make_spack_env(sandbox)
    return run_bash(spack_cmd(spack_root, args, env_vars), dry_run=dry_run, check=check)


def spack_user_cfg_dir(spack_root: str, *, dry_run: bool, sandbox: Path | None = None) -> Path:
    # Spack >=1.2: print-file gives us the exact file path.
    r = spack_run(
        spack_root,
        ["config", "--scope", "user", "edit", "--print-file", "config"],
        dry_run=dry_run,
        check=False,
        sandbox=sandbox,
    )
    if dry_run:
        # Best guess; used only for printing in dry-run mode
        return (sandbox / ".spack") if sandbox else (Path.home() / ".spack")
    if r.returncode != 0:
        raise SystemExit(
            "ERROR: couldn't determine user config dir (spack config edit --print-file config failed)"
        )
    path = r.stdout.strip().splitlines()[-1].strip()
    if not path:
        raise SystemExit("ERROR: spack didn't print a config file path for user scope")
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.parent


def ensure_spack(
    spack_root: str, spack_repo: str, *, dry_run: bool, sandbox: Path | None = None
) -> None:
    git_clone_if_missing(f"git@github.com:{spack_repo}.git", Path(spack_root), dry_run=dry_run)
    r = spack_run(spack_root, ["--version"], dry_run=dry_run, check=False, sandbox=sandbox)
    if dry_run:
        eprint("==> Spack available: spack")
    else:
        eprint("==> Spack available: spack")
        if r.stdout.strip():
            eprint(r.stdout.strip())


def ensure_repos(
    spack_root: str,
    spack_packages_dir: str,
    spack_packages_repo: str,
    *,
    dry_run: bool,
    sandbox: Path | None = None,
) -> None:
    git_clone_if_missing(
        f"git@github.com:{spack_packages_repo}.git",
        Path(spack_packages_dir),
        dry_run=dry_run,
    )

    base = sandbox if sandbox else Path.home()
    geosesm_dir = base / "geosesm-spack"
    git_clone_if_missing(
        "git@github.com:GMAO-SI-Team/geosesm-spack.git", geosesm_dir, dry_run=dry_run
    )

    user_cfg = spack_user_cfg_dir_from_env(sandbox)
    if dry_run:
        eprint(f"[dry-run] would mkdir {user_cfg}")
    else:
        user_cfg.mkdir(parents=True, exist_ok=True)
    repos_yaml = user_cfg / "repos.yaml"
    eprint(f"==> Writing {repos_yaml}")
    content = f"""repos:
  builtin:
    destination: {spack_packages_dir}
  geosesm: {geosesm_dir}/spack_repo/geosesm
"""
    if dry_run:
        eprint("[dry-run] would write:\n" + content.rstrip())
    else:
        repos_yaml.write_text(content)


def backup_user_cfg(user_cfg: Path, *, dry_run: bool) -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = user_cfg / "bootstrap-backups" / ts
    eprint(f"==> Backing up user config files from: {user_cfg}")
    eprint(f"==> Backup dir: {bdir}")
    if not dry_run:
        bdir.mkdir(parents=True, exist_ok=True)

    for name in [
        "repos.yaml",
        "packages.yaml",
        "concretizer.yaml",
        "config.yaml",
        "compilers.yaml",
    ]:
        src = user_cfg / name
        if src.exists():
            if dry_run:
                eprint(f"[dry-run] cp -a {src} {bdir / name}")
            else:
                (bdir / name).write_bytes(src.read_bytes())
            eprint(f"==>   backed up: {name}")
    return bdir


def reset_user_cfg(spack_root: str, *, dry_run: bool, sandbox: Path | None = None) -> None:
    user_cfg = spack_user_cfg_dir(spack_root, dry_run=dry_run, sandbox=sandbox)
    backup_user_cfg(user_cfg, dry_run=dry_run)

    eprint(f"==> Resetting (removing) user config files managed by this tool in: {user_cfg}")
    for name in ["repos.yaml", "packages.yaml", "concretizer.yaml"]:
        p = user_cfg / name
        if p.exists():
            if dry_run:
                eprint(f"[dry-run] rm -f {p}")
            else:
                p.unlink()
            eprint(f"==>   removed: {name}")
        else:
            eprint(f"==>   absent:  {name}")


def ensure_tcsh_external_via_spack_python(
    spack_root: str, *, dry_run: bool, brew_prefix: str, sandbox: Path | None = None
) -> None:
    """
    Use `spack python` (Spack's Python environment) to edit the packages config.

    IMPORTANT: Some Spack versions/wrappers behave oddly with `spack python -c` and multi-line code
    (can trigger SyntaxError about "single statement"). To be robust, we write a temporary script
    and run `spack python <script>`.
    """
    import tempfile

    # Determine tcsh version from brew if possible (best-effort)
    tcsh_ver = "6.24.16"
    if not dry_run:
        brew = shutil_which("brew") or "brew"
        r = run([brew, "list", "--versions", "tcsh"], dry_run=False, check=False)
        if r.returncode == 0:
            parts = r.stdout.strip().split()
            if len(parts) >= 2:
                tcsh_ver = parts[1]

    eprint(f"==> Ensuring tcsh external (via spack python): tcsh@{tcsh_ver} prefix={brew_prefix}")

    code = f"""
import spack.config as cfg

want_spec = 'tcsh@{tcsh_ver}'
want_prefix = '{brew_prefix}'

# Read user-scope packages (may be None)
pkgs = cfg.get('packages', scope='user') or {{}}
tcsh = (pkgs.get('tcsh') or {{}})
exts = list(tcsh.get('externals') or [])

def same(e):
    try:
        return e.get('spec') == want_spec and e.get('prefix') == want_prefix
    except Exception:
        return False

if not any(same(e) for e in exts):
    exts.append({{'spec': want_spec, 'prefix': want_prefix}})

tcsh['externals'] = exts
pkgs['tcsh'] = tcsh

cfg.set('packages', pkgs, scope='user')
print("ok")
""".lstrip()

    if dry_run:
        eprint("[dry-run] would run: spack python <tempfile> (update user-scope packages config)")
        return

    # Write temp script and run it
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as tf:
            tf.write(code)
            tmp_path = tf.name

        env_vars = make_spack_env(sandbox)
        proc = run_bash(
            f"{spack_bash_prefix(spack_root, env_vars)} && spack python {shlex.quote(tmp_path)}",
            dry_run=dry_run,
            check=False,
        )

        if proc.returncode != 0:
            # Surface spack/python stderr so debugging is possible
            if proc.stdout.strip():
                eprint(proc.stdout.rstrip())
            if proc.stderr.strip():
                eprint(proc.stderr.rstrip())
            raise SystemExit(
                f"ERROR: spack python failed while ensuring tcsh external (exit {proc.returncode})"
            )

        if proc.stdout.strip():
            eprint(proc.stdout.strip())

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def ensure_config(
    spack_root: str,
    *,
    dry_run: bool,
    sandbox: Path | None = None,
    requested_target: str | None = None,
) -> str | None:
    """
    Configure Spack: build_jobs, environments_root, compilers, externals, concretizer.

    On Linux, also enforces a trusted GCC version (see LINUX_TRUSTED_GCC_MIN_VERSIONS).
    On macOS, enforces Apple clang >= MACOS_MIN_APPLE_CLANG_MAJOR and a trusted
    Homebrew gfortran version (see MACOS_TRUSTED_GFORTRAN_MIN_VERSIONS).

    Returns the highest qualifying compiler spec (e.g. 'gcc@15.2.0') so callers
    can auto-select it for environment creation.  Returns None when not applicable.
    """
    eprint("==> Setting build_jobs=6")
    spack_run(
        spack_root,
        ["config", "--scope", "user", "add", "config:build_jobs:6"],
        dry_run=dry_run,
        check=False,
        sandbox=sandbox,
    )

    base = sandbox if sandbox else Path.home()
    env_root = str(base / "spack-envs")
    eprint(f"==> Setting environments_root={env_root}")
    spack_run(
        spack_root,
        ["config", "--scope", "user", "add", f"config:environments_root:{env_root}"],
        dry_run=dry_run,
        check=False,
        sandbox=sandbox,
    )

    # On Linux, enforce a trusted GCC version (LINUX_TRUSTED_GCC_MIN_VERSIONS) before
    # finding compilers. This also returns the spec for the highest qualifying
    # version so main() can auto-select it for environment creation.
    compiler_spec: str | None = None
    if is_linux():
        compiler_spec = require_linux_gcc(dry_run=dry_run)
    elif is_mac_os():
        # On macOS, enforce Apple clang >= MACOS_MIN_APPLE_CLANG_MAJOR and a
        # trusted Homebrew gfortran version.  Returns the gfortran spec so
        # main() can auto-select it for Fortran in environment creation.
        compiler_spec = require_macos_compilers(dry_run=dry_run)

    eprint("==> Finding compilers")
    spack_run(spack_root, ["compiler", "find"], dry_run=dry_run, check=False, sandbox=sandbox)

    eprint("==> Finding externals (with excludes) + bash")
    ext_cmd = ["external", "find"]
    for ex in EXTERNAL_FIND_EXCLUDES:
        ext_cmd.extend(["--exclude", ex])
    spack_run(spack_root, ext_cmd, dry_run=dry_run, check=False, sandbox=sandbox)
    spack_run(
        spack_root,
        ["external", "find", "bash"],
        dry_run=dry_run,
        check=False,
        sandbox=sandbox,
    )

    # Ensure tcsh external using spack python rather than config add
    if is_mac_os():
        brew = shutil_which("brew") or str(Path.home() / ".homebrew" / "bin" / "brew")
        brew_prefix = "/opt/homebrew"
        if not dry_run:
            r = run([brew, "--prefix"], dry_run=False, check=False)
            if r.returncode == 0 and r.stdout.strip():
                brew_prefix = r.stdout.strip()
        ensure_tcsh_external_via_spack_python(
            spack_root, dry_run=dry_run, brew_prefix=brew_prefix, sandbox=sandbox
        )

    user_cfg = spack_user_cfg_dir_from_env(sandbox)
    if dry_run:
        eprint(f"[dry-run] would mkdir {user_cfg}")
    else:
        user_cfg.mkdir(parents=True, exist_ok=True)

    # Write packages:all:target if a target override is needed (explicit request
    # or Apple clang forced a downgrade below the host target).
    packages_all_target = resolve_packages_all_target(
        spack_root, requested_target, dry_run=dry_run, sandbox=sandbox
    )
    if packages_all_target:
        eprint(f"==> Writing packages:all:target: [{packages_all_target}] to packages.yaml")
        spack_run(
            spack_root,
            [
                "config",
                "--scope",
                "user",
                "add",
                f"packages:all:target:[{packages_all_target}]",
            ],
            dry_run=dry_run,
            check=False,
            sandbox=sandbox,
        )

    concretizer_yaml = user_cfg / "concretizer.yaml"
    eprint(f"==> Writing concretizer.yaml (reuse: false) -> {concretizer_yaml}")
    content = "concretizer:\n  reuse: false\n"
    if dry_run:
        eprint("[dry-run] would write:\n" + content.rstrip())
    else:
        concretizer_yaml.write_text(content)

    return compiler_spec


def auto_env_name(base: str, compiler: str | None, python: str | None) -> str:
    """
    Generate environment name from toolchain specs.
    Examples:
      gcc@14, 3.12   -> geos-gcc14-py312
      gcc@15, None   -> geos-gcc15
      None, 3.12     -> geos-py312
      None, None     -> geos
    """
    parts = [base]

    if compiler:
        # Extract compiler name and version from specs like "gcc@14" or "apple-clang@17.0.6"
        comp = compiler.strip()
        if "@" in comp:
            name, ver = comp.split("@", 1)
            # Simplify version to major version for common compilers
            ver_parts = ver.split(".")
            if name in ("gcc", "gfortran", "nag"):
                ver_short = ver_parts[0]
            elif name in ("apple-clang", "clang"):
                ver_short = ver_parts[0]
            else:
                ver_short = ver_parts[0]
            parts.append(f"{name.replace('-', '')}{ver_short}")
        else:
            # No version specified, just use compiler name
            parts.append(comp.replace("-", ""))

    if python:
        # Extract Python version from specs like "3.12", "@3.11", "3.10.2"
        py = python.strip().lstrip("@")
        py_parts = py.split(".")
        # Use major.minor for Python
        if len(py_parts) >= 2:
            py_short = f"py{py_parts[0]}{py_parts[1]}"
        else:
            py_short = f"py{py_parts[0]}"
        parts.append(py_short)

    return "-".join(parts)


def create_env(
    spack_root: str,
    env_dir: Path,
    *,
    dry_run: bool,
    env_name: str = "geos",
    compiler: str | None = None,
    compiler_c: str | None = None,
    compiler_fortran: str | None = None,
    python: str | None = None,
    python_optimizations: bool = False,
    sandbox: Path | None = None,
    custom_spec: str | None = None,
    target: str | None = None,
    view: bool = False,
) -> None:
    if env_dir.exists():
        eprint(f"==> Environment already exists: {env_dir}")
        return

    eprint(f"==> Creating environment: {env_name}")

    # Validate apple-clang usage
    if compiler and "apple-clang" in compiler and not (compiler_c or compiler_fortran):
        raise SystemExit(
            "ERROR: apple-clang does not include a Fortran compiler.\n"
            "For Fortran support, use one of these options:\n"
            "  1. --compiler gcc@15 (recommended: auto-uses apple-clang for C/C++, gcc for Fortran)\n"
            "  2. --compiler-c apple-clang@17 --compiler-fortran gcc@15 (explicit control)\n"
            "  3. --compiler-fortran gcc@15 (uses default apple-clang for C/C++, gcc for Fortran)"
        )

    # Determine compiler strategy
    # On macOS, if user specifies gcc, use apple-clang for C/C++ and gcc for Fortran (best practice)
    # unless explicit overrides are provided
    c_spec = None
    fortran_spec = None

    if compiler_c or compiler_fortran:
        # Explicit overrides - validate apple-clang + fortran combination
        if compiler_fortran and "apple-clang" in compiler_fortran:
            raise SystemExit(
                "ERROR: apple-clang does not include a Fortran compiler.\n"
                "Use gcc, gfortran, or nag for --compiler-fortran."
            )
        c_spec = compiler_c
        fortran_spec = compiler_fortran
    elif compiler:
        # Smart defaults based on compiler choice
        if is_mac_os() and compiler.startswith(("gcc", "gfortran")):
            # macOS + gcc: use apple-clang for C/C++, gcc for Fortran
            # Find the default apple-clang version
            c_spec = "apple-clang"  # Spack will find the default version
            fortran_spec = compiler
            eprint(f"==> macOS detected: using apple-clang for C/C++, {compiler} for Fortran")
        else:
            # Use specified compiler for all languages
            c_spec = compiler
            fortran_spec = compiler

    # Resolve partial compiler specs (e.g. gcc@15) to concrete versions (e.g. gcc@15.2.0).
    # Spack v1 requires fortran/c/cxx compiler references in specs to be concrete or external;
    # a partial version is treated as a range and fails with:
    #   "Only external, or concrete, compilers are allowed for the fortran language"
    packages_yaml_path = spack_user_cfg_dir_from_env(sandbox) / "packages.yaml"
    if c_spec:
        c_spec = resolve_concrete_compiler_spec(c_spec, packages_yaml_path)
    if fortran_spec:
        fortran_spec = resolve_concrete_compiler_spec(fortran_spec, packages_yaml_path)

    # Concretizer policy:
    # - no constraints   -> unify: true (single solve)
    # - compiler only    -> unify: when_possible (macOS compatibility)
    # - python or both   -> unify: false (much faster, avoids complex SAT problems)
    has_compiler_constraint = bool(compiler or c_spec or fortran_spec)
    if python:
        unify_val = "false"
    elif has_compiler_constraint:
        unify_val = "when_possible"
    else:
        unify_val = "true"

    # Specs: either individual packages or a custom spec for dependency-only workflow
    # Build compiler constraint suffix if needed (propagates to all dependencies).
    # NOTE: target is NOT embedded in the spec string — it is expressed via a
    # packages.all.require in the packages block. Embedding target= directly in a
    # spec that uses a split compiler constraint (fortran=gcc@X) triggers a Spack
    # concretizer bug: "Only external, or concrete, compilers are allowed for the
    # fortran language".
    compiler_suffix = ""
    # A BundlePackage has no build phase. In Spack 1.3, putting a single GCC
    # compiler constraint directly on a bundle root (for example
    # ``geosgcm-deps %gcc@15.2.0``) makes it implicitly depend on that compiler
    # package. GCC cannot be both the selected compiler and a dependency of
    # the bundle, so Linux concretization fails with "cannot depend on gcc".
    #
    # Keep macOS's split Apple Clang/GCC constraint: it selects Apple Clang for
    # C/C++ and GCC for Fortran, and does not use the problematic single-GCC
    # root constraint.
    single_gcc_bundle_constraint = (
        is_linux()
        and _spec_is_bundle(custom_spec or DEFAULT_SPEC)
        and c_spec == fortran_spec
        and bool(c_spec)
        and c_spec.startswith(("gcc", "gfortran"))
    )
    apply_compiler_constraint = not single_gcc_bundle_constraint
    if not apply_compiler_constraint:
        eprint("==> Not applying single-GCC compiler constraint to BundlePackage root")
    elif c_spec and fortran_spec:
        # Both C/C++ and Fortran specified
        if c_spec == fortran_spec:
            # Same compiler for everything — no language splitting needed
            compiler_suffix = f" %{c_spec}"
        elif is_mac_os() and fortran_spec.startswith("gcc"):
            # macOS with gcc for Fortran: use conditional constraints to apply correct compiler per language
            compiler_suffix = f" %[when='%c'] c={c_spec} %[when='%cxx'] cxx={c_spec} %[when='%fortran'] fortran={fortran_spec}"
        else:
            # Both compilers, use languages constraint
            compiler_suffix = f" %{c_spec} languages:=c,cxx %{fortran_spec} languages:=fortran"
    elif c_spec:
        # C/C++ only
        compiler_suffix = f" %{c_spec}"
    elif fortran_spec:
        # Fortran only
        if is_mac_os() and fortran_spec.startswith("gcc"):
            # macOS with gcc: explicitly set apple-clang for C/C++, gcc for Fortran
            compiler_suffix = f" %[when='%c'] c=apple-clang %[when='%cxx'] cxx=apple-clang %[when='%fortran'] fortran={fortran_spec}"
        else:
            compiler_suffix = f" %{fortran_spec} languages:=fortran"

    if custom_spec:
        spec_lines = [f"    - {custom_spec}{compiler_suffix}"]
        eprint(
            f"==> Using custom spec '{custom_spec}' (for 'spack install --only dependencies' workflow)"
        )
    else:
        # Default: use geosgcm
        spec_lines = [f"    - {DEFAULT_SPEC}{compiler_suffix}"]
        eprint(f"==> Using default spec '{DEFAULT_SPEC}'")
    specs = "\n".join(spec_lines)

    # Build packages block.
    # - openmpi variants are always required for a working GEOS build.
    # - python version/optimizations are only added when explicitly requested.
    # Target is written to the global user-scope packages.yaml by ensure_config,
    # not per-environment.
    lines = ["  packages:"]
    lines.append("    openmpi:")
    lines.append("      require: '+fortran +internal-hwloc +internal-libevent +internal-pmix'")
    if python or python_optimizations:
        lines.append("    python:")
        if python:
            # Ensure Python version has @ prefix for proper spec format
            py_spec = python.strip()
            if not py_spec.startswith("@"):
                py_spec = f"@{py_spec}"
            # Add variant if optimizations requested
            if python_optimizations:
                py_spec = f"{py_spec}+optimizations"
            lines.append(f"      require: '{py_spec}'")
        elif python_optimizations:
            # Just the variant, no version constraint
            lines.append("      require: '+optimizations'")
    packages_block = "\n" + "\n".join(lines) + "\n"

    # Add compiler env vars to work around Spack bug #51855.
    # Always set CC/CXX/FC so the environment picks up the correct system compilers
    # regardless of whether a compiler constraint was explicitly specified.
    # Paths are read from packages.yaml (written by `spack external find` during
    # ensure_config) so they reflect exactly what Spack detected, not PATH order.
    env_vars_block = ""
    compiler_paths = find_compiler_paths_from_packages_yaml(
        packages_yaml_path, c_spec, fortran_spec
    )
    if compiler_paths:
        eprint("==> Adding compiler env vars (workaround for Spack bug #51855):")
        lines = ["  env_vars:"]
        lines.append("    set:")
        for var, path in sorted(compiler_paths.items()):
            lines.append(f"      {var}: {path}")
            eprint(f"    {var}={path}")
        env_vars_block = "\n" + "\n".join(lines) + "\n"

    # BundlePackages have no build products of their own; a merged view just
    # wastes symlinks and slows install.  For non-bundle specs the view is
    # useful when pointing external tools at a single prefix tree.
    view_val = "true" if view else "false"

    content = f"""spack:
  specs:
{specs}
  concretizer:
    unify: {unify_val}
{packages_block}{env_vars_block}  view: {view_val}
"""

    spack_yaml = env_dir / "spack.yaml"

    if dry_run:
        eprint(f"[dry-run] would create directory {env_dir}")
        eprint(f"[dry-run] would write {spack_yaml}")
        eprint(f"[dry-run]   C/C++ compiler: {c_spec or 'default'}")
        eprint(f"[dry-run]   Fortran compiler: {fortran_spec or 'default'}")
        eprint(f"[dry-run]   Python: {python or 'default'}")
        eprint(f"[dry-run]   Python optimizations: {python_optimizations}")
        eprint(f"[dry-run]   Target: {target or 'default'}")
        return

    env_dir.mkdir(parents=True, exist_ok=True)
    spack_yaml.write_text(content)
    eprint(f"==> Wrote {spack_yaml}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="""
Bootstrap Spack with fork-aware layout and environment management.

Running with no arguments will interactively prompt for Spack choice and perform
a full bootstrap (brew + spack + repos + config + default env).
        """,
        epilog="""
EXAMPLES:
  %(prog)s
      Default: interactive prompt, then full bootstrap (all subcommand)

  %(prog)s --spack official setup
      Set up official Spack without creating an environment

  %(prog)s --spack mathomp4 all
      Full bootstrap with mathomp4 fork including default environment

  %(prog)s --spack mathomp4 env-create --auto-name --compiler gcc@15 --python 3.12
      Create environment named 'geos-gcc15-py312' with compiler and Python constraints

  %(prog)s --spack official env-create --print-effective-target-only
      Print resolved target and exit without environment creation

  %(prog)s --spack fork --fork jcsda setup
      Set up a custom fork (jcsda/spack and jcsda/spack-packages)

  %(prog)s --dry-run --spack official config-clean
      Preview what config-clean would do without making changes

For more details, see the docstring at the top of this script.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    p.add_argument("--dry-run", action="store_true", help="Print actions without changing anything")
    p.add_argument(
        "--sandbox",
        type=Path,
        default=None,
        help="Install everything in this directory (for testing/isolation). "
        "Repos, config, and environments will all go under this path.",
    )

    # Spack selection
    p.add_argument(
        "--spack",
        choices=["interactive", "official", "mathomp4", "fork"],
        default="interactive",
        help="Which Spack to use (default: interactive, prompts user)",
    )
    p.add_argument("--fork", default=None, help="Fork org/user name (required with --spack fork)")
    p.add_argument("--spack-repo", default=None, help="Override spack repo (e.g., org/spack)")
    p.add_argument(
        "--spack-packages-repo",
        default=None,
        help="Override spack-packages repo (e.g., org/spack-packages)",
    )

    sub = p.add_subparsers(dest="cmd", required=False)

    # Simple commands with no extra args
    sub.add_parser("all", help="Full bootstrap: brew + spack + repos + config + default env")
    sub.add_parser("brew", help="Install Homebrew prerequisites only")
    sub.add_parser("spack", help="Clone Spack repository only")
    sub.add_parser("repos", help="Clone and configure spack-packages and geosesm-spack repos")
    sub.add_parser("config", help="Configure Spack (build_jobs, compilers, externals, concretizer)")
    sub.add_parser(
        "setup",
        help="Complete setup without environment: brew + spack + repos + config",
    )
    sub.add_parser("env", help="Create default 'geos' environment")
    sub.add_parser("reset", help="Backup and remove user-scope config files")
    sub.add_parser("config-clean", help="Reset config, then rebuild repos and config from scratch")

    # env-create: create a named environment (optionally with compiler constraint)
    p_envc = sub.add_parser(
        "env-create",
        help="Create a custom Spack environment with optional compiler/Python constraints",
        description="""
Create a managed Spack environment under ~/spack-envs with optional toolchain constraints.
Use --auto-name to generate environment names from specs (e.g., geos-gcc15-py312).

On macOS, when --compiler gcc@X is specified, the script automatically uses apple-clang
for C/C++ and gcc for Fortran (best practice). Use --compiler-c and --compiler-fortran
for explicit control.

If --target is omitted on Apple Silicon, the script auto-selects a conservative target
based on host architecture and Apple clang compatibility.
        """,
        epilog="""
EXAMPLES:
  %(prog)s --spack mathomp4 env-create
      Create default 'geos' environment

  %(prog)s --spack mathomp4 env-create --name my-project
      Create environment named 'my-project'

  %(prog)s --spack mathomp4 env-create --auto-name --compiler gcc@15 --python 3.12
      Create 'geos-gcc15-py312' (macOS: apple-clang for C/C++, gcc@15 for Fortran)

  %(prog)s --spack mathomp4 env-create --auto-name --compiler apple-clang@17
      Create 'geos-appleclang17' environment with apple-clang for all languages

  %(prog)s --spack mathomp4 env-create --auto-name --compiler-fortran gcc@15
      Create 'geos-gcc15' with default C/C++ and gcc@15 for Fortran

  %(prog)s --spack mathomp4 env-create --compiler-c apple-clang@17 --compiler-fortran gcc@15
      Explicit control: apple-clang for C/C++, gcc for Fortran

  %(prog)s --spack mathomp4 env-create --print-effective-target --auto-name --compiler gcc@15
      Print resolved target, then create environment

  %(prog)s --spack mathomp4 env-create --print-effective-target-only
      Print resolved target and exit (no repo/config/env changes)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_envc.add_argument("--name", default="geos", help="Environment name (default: geos)")
    p_envc.add_argument(
        "--auto-name",
        action="store_true",
        help="Auto-generate name from compiler/python specs (e.g., geos-gcc15-py312)",
    )
    p_envc.add_argument(
        "--compiler",
        default=None,
        help="Compiler constraint (e.g., gcc@15, apple-clang@17). "
        "On macOS, gcc@X uses apple-clang for C/C++ and gcc for Fortran.",
    )
    p_envc.add_argument(
        "--compiler-c",
        default=None,
        help="Explicit C/C++ compiler (overrides --compiler for C/C++)",
    )
    p_envc.add_argument(
        "--compiler-fortran",
        default=None,
        help="Explicit Fortran compiler (overrides --compiler for Fortran)",
    )
    p_envc.add_argument(
        "--python",
        default=None,
        help="Python version constraint (e.g., 3.12, @3.11, 3.10.2)",
    )
    p_envc.add_argument(
        "--python-optimizations",
        action="store_true",
        help="Build Python with +optimizations variant (enables PGO for better performance, but slower build)",
    )
    p_envc.add_argument(
        "--spec",
        default=None,
        help="Use a custom spec (e.g., 'geosgcm', 'mapl') instead of individual packages. "
        "Intended for 'spack install --only dependencies' workflow. "
        "Automatically adds CC/CXX/FC env vars (Spack bug #51855 workaround).",
    )
    p_envc.add_argument(
        "--target",
        default=None,
        help="Spack target architecture (e.g., 'x86_64_v3', 'icelake', 'm1'). "
        "Constrains all packages to build for this specific microarchitecture. "
        "On Apple Silicon, if omitted, target is auto-selected from host+compiler compatibility. "
        "If provided, values above Apple clang capability are rejected.",
    )
    p_envc.add_argument(
        "--print-effective-target",
        action="store_true",
        help="Print the resolved target used for environment creation (after auto-detection/validation).",
    )
    p_envc.add_argument(
        "--print-effective-target-only",
        action="store_true",
        help="Print the resolved target and exit without creating or modifying an environment.",
    )
    p_envc.add_argument(
        "--view",
        action="store_true",
        default=False,
        help="Enable a merged filesystem view in the environment (default: disabled). "
        "Useful if you need a single prefix tree, but adds overhead for bundle packages.",
    )

    p.set_defaults(cmd="all")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if os.environ.get("SPACK_ENV"):
        eprint(
            "ERROR: SPACK_ENV is set — you appear to be inside an active Spack environment.\n"
            f"       Active environment: {os.environ['SPACK_ENV']}\n"
            "       Please run 'spack env deactivate' before using this bootstrapper."
        )
        return 1

    if not is_supported_platform():
        eprint(f"ERROR: this bootstrap currently targets {list(platforms.values())}.")
        return 2

    dry_run = bool(args.dry_run)
    sandbox = args.sandbox

    if sandbox:
        sandbox = sandbox.expanduser().resolve()
        eprint(f"==> Sandbox mode: using {sandbox}")
        if not dry_run:
            sandbox.mkdir(parents=True, exist_ok=True)

    # Brew is always useful early
    if is_mac_os():
        brew = ensure_homebrew(dry_run=dry_run)

    # Will be set by ensure_config to the highest trusted compiler spec,
    # e.g. 'gcc@15.2.0' (Linux GCC) or 'gcc@15.2.0' (macOS Homebrew gfortran).
    auto_compiler_spec: str | None = None

    # Determine spack choice
    spack_choice = args.spack
    fork = args.fork
    if spack_choice == "interactive":
        spack_choice, fork = pick_spack_interactive()

    layout = spack_layout(spack_choice, fork, args.spack_repo, args.spack_packages_repo, sandbox)
    spack_root = layout["spack_root"]
    spack_packages_dir = layout["spack_packages_dir"]

    eprint("==> Selected:")
    eprint(f"  SPACK_REPO          = {layout['spack_repo']}")
    eprint(f"  SPACK_ROOT          = {spack_root}")
    eprint(f"  SPACKPACKAGES_REPO  = {layout['spack_packages_repo']}")
    eprint(f"  SPACKPACKAGES_DIR   = {spack_packages_dir}")
    if sandbox:
        eprint(f"  USER_CONFIG         = {sandbox / '.spack'}")
        eprint(f"  ENVIRONMENTS_ROOT   = {sandbox / 'spack-envs'}")

    cmd = args.cmd

    if cmd == "env-create" and getattr(args, "print_effective_target_only", False):
        target = resolve_effective_target(
            spack_root,
            getattr(args, "target", None),
            dry_run=dry_run,
            sandbox=sandbox,
        )
        eprint(f"==> Effective target: {target if target else 'default (Spack host target)'}")
        return 0

    if cmd in ("all", "brew", "setup"):
        if is_mac_os():
            ensure_brew_prereqs(brew, dry_run=dry_run)
        if cmd == "brew":
            return 0

    if cmd in (
        "all",
        "spack",
        "repos",
        "config",
        "setup",
        "env",
        "reset",
        "config-clean",
        "env-create",
    ):
        ensure_spack(spack_root, layout["spack_repo"], dry_run=dry_run, sandbox=sandbox)

    if cmd == "spack":
        return 0

    if cmd in ("reset", "config-clean"):
        reset_user_cfg(spack_root, dry_run=dry_run, sandbox=sandbox)
        if cmd == "reset":
            return 0

    if cmd in ("all", "repos", "setup", "config-clean", "env-create"):
        ensure_repos(
            spack_root,
            spack_packages_dir,
            layout["spack_packages_repo"],
            dry_run=dry_run,
            sandbox=sandbox,
        )
        if cmd == "repos":
            return 0

    if cmd in ("all", "config", "setup", "config-clean", "env-create"):
        auto_compiler_spec = ensure_config(
            spack_root,
            dry_run=dry_run,
            sandbox=sandbox,
            requested_target=getattr(args, "target", None),
        )
        if cmd == "config":
            return 0
        if cmd == "setup":
            print_minimal_advice(spack_root, None, sandbox)
            return 0

    if cmd in ("all", "env", "env-create"):
        # Managed environments live under environments_root (we set this to ~/spack-envs or <sandbox>/spack-envs).
        base = sandbox if sandbox else Path.home()
        env_root = base / "spack-envs"
        env_name = "geos"
        compiler = None
        compiler_c = None
        compiler_fortran = None
        python = None
        python_optimizations = False
        target = None
        view = False
        if cmd == "env-create":
            compiler = getattr(args, "compiler", None)
            compiler_c = getattr(args, "compiler_c", None)
            compiler_fortran = getattr(args, "compiler_fortran", None)
            # If no explicit compiler was given, auto-select the highest trusted compiler
            # returned by ensure_config (GCC on Linux, Homebrew gcc/gfortran on macOS).
            if not compiler and not compiler_c and not compiler_fortran:
                if auto_compiler_spec:
                    eprint(f"==> Auto-selecting compiler {auto_compiler_spec} for environment")
                    compiler = auto_compiler_spec
            python = getattr(args, "python", None)
            python_optimizations = getattr(args, "python_optimizations", False)
            target = resolve_effective_target(
                spack_root,
                getattr(args, "target", None),
                dry_run=dry_run,
                sandbox=sandbox,
            )
            if getattr(args, "print_effective_target", False):
                eprint(
                    f"==> Effective target: {target if target else 'default (Spack host target)'}"
                )
            custom_spec = getattr(args, "spec", None)
            view = getattr(args, "view", False)
            auto_name = getattr(args, "auto_name", False)
            if auto_name:
                # For auto-naming, use the effective compiler (for fortran if split)
                name_compiler = compiler_fortran or compiler
                env_name = auto_env_name("geos", name_compiler, python)
            else:
                env_name = getattr(args, "name", "geos")
        else:
            custom_spec = None
            # On 'all' or 'env' commands, auto-select the highest trusted compiler
            # if ensure_config returned one and no compiler was explicitly requested.
            if auto_compiler_spec:
                eprint(f"==> Auto-selecting compiler {auto_compiler_spec} for environment")
                compiler = auto_compiler_spec
        env_path = env_root / env_name

        create_env(
            spack_root,
            env_path,
            dry_run=dry_run,
            env_name=env_name,
            compiler=compiler,
            compiler_c=compiler_c,
            compiler_fortran=compiler_fortran,
            python=python,
            python_optimizations=python_optimizations,
            sandbox=sandbox,
            custom_spec=custom_spec,
            target=target,
            view=view,
        )

        if cmd == "env-create":
            spec_name = custom_spec if custom_spec else DEFAULT_SPEC
            print_minimal_advice(spack_root, env_name, sandbox, spec_name)
            return 0

        if cmd == "env":
            return 0

    # If we reached here via all or config-clean, print minimal guidance.
    if cmd in ("all", "config-clean"):
        # Only mention env steps if the default env exists
        base = sandbox if sandbox else Path.home()
        env_path = base / "spack-envs" / "geos"
        if env_path.exists() and (env_path / "spack.yaml").exists():
            print_minimal_advice(spack_root, "geos", sandbox)
        else:
            print_minimal_advice(spack_root, None, sandbox)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
