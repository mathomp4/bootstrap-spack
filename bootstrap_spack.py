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
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

BREW_PKGS = ["coreutils", "gcc", "git", "wget", "bash", "tcsh", "cmake", "openssl", "rust"]

EXTERNAL_FIND_EXCLUDES = [
    "bison", "openssl", "gmake", "m4", "curl", "python", "gettext", "perl", "meson"
]

# Default spec for environments (GEOSgcm and its dependencies)
DEFAULT_SPEC = "geosgcm"


def find_system_compiler_paths(c_spec: str | None, fortran_spec: str | None) -> dict[str, str]:
    """
    Find actual system paths to compilers (not Spack wrappers).
    Returns dict with CC, CXX, and/or FC set to system paths.
    """
    env_vars = {}
    
    # C/C++ compiler
    if c_spec and 'gcc' in c_spec:
        # gcc-based C/C++
        # Extract version if specified (gcc@15 -> gcc-15)
        if '@' in c_spec:
            name, ver = c_spec.split('@', 1)
            ver_major = ver.split('.')[0]
            gcc_name = f"gcc-{ver_major}"
            gxx_name = f"g++-{ver_major}"
        else:
            gcc_name = "gcc"
            gxx_name = "g++"
        
        gcc_path = shutil_which(gcc_name)
        gxx_path = shutil_which(gxx_name)
        if gcc_path:
            env_vars['CC'] = gcc_path
        if gxx_path:
            env_vars['CXX'] = gxx_path
    else:
        # Default to clang/clang++ (apple-clang or system default)
        clang_path = shutil_which('clang')
        clangxx_path = shutil_which('clang++')
        if clang_path:
            env_vars['CC'] = clang_path
        if clangxx_path:
            env_vars['CXX'] = clangxx_path
    
    # Fortran compiler
    if fortran_spec:
        if 'gcc' in fortran_spec or 'gfortran' in fortran_spec:
            # Extract version for gfortran
            if '@' in fortran_spec:
                _, ver = fortran_spec.split('@', 1)
                ver_major = ver.split('.')[0]
                gfortran_name = f"gfortran-{ver_major}"
            else:
                gfortran_name = "gfortran"
            
            gfortran_path = shutil_which(gfortran_name)
            if gfortran_path:
                env_vars['FC'] = gfortran_path
    
    return env_vars


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)



def print_minimal_advice(spack_root: str, env_name: str | None = None, sandbox: Path | None = None, spec: str | None = None) -> None:
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
            eprint(f"This environment will install dependencies of: {spec}")
            eprint("")
        eprint("Next steps:")
        eprint("  spack env list")
        eprint(f"  spack env activate {env_name}")
        eprint("  spack concretize")
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


def run(cmd: Sequence[str], *, dry_run: bool, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
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
        r = run([brew, "list", "--formula", pkg], dry_run=dry_run, check=False)
        if r.returncode == 0:
            eprint(f"==> brew: {pkg} already installed")
        else:
            eprint(f"==> brew: installing {pkg}")
            run([brew, "install", pkg], dry_run=dry_run)


def git_clone_if_missing(url: str, dest: Path, *, dry_run: bool) -> None:
    if (dest / ".git").exists():
        eprint(f"==> Repo already cloned: {dest}")
        return
    eprint(f"==> Cloning {url} -> {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "-c", "feature.manyFiles=true", url, str(dest)], dry_run=dry_run)


def spack_layout(spack_choice: str, fork: str | None, spack_repo: str | None, spack_packages_repo: str | None, sandbox: Path | None = None) -> dict:
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


def spack_bash_prefix(spack_root: str, env_vars: dict | None = None) -> str:
    # Using bash -lc so we can source setup-env.sh.
    # Include any environment variable overrides before sourcing.
    prefix = ""
    if env_vars:
        for key, val in env_vars.items():
            prefix += f"export {key}={shlex.quote(str(val))} && "
    return f"{prefix}source {shlex.quote(spack_root)}/share/spack/setup-env.sh"


def make_spack_env(sandbox: Path | None = None) -> dict[str, str]:
    """Create environment dict for running spack commands."""
    env = {}
    if sandbox:
        env["SPACK_USER_CONFIG_PATH"] = str(sandbox / ".spack")
    return env


def spack_cmd(spack_root: str, args: Sequence[str], env_vars: dict | None = None) -> str:
    # Return a bash -lc string that runs spack with given args.
    return f"{spack_bash_prefix(spack_root, env_vars)} && spack {' '.join(shlex.quote(a) for a in args)}"


def spack_run(spack_root: str, args: Sequence[str], *, dry_run: bool, check: bool = True, sandbox: Path | None = None) -> subprocess.CompletedProcess:
    env_vars = make_spack_env(sandbox)
    return run_bash(spack_cmd(spack_root, args, env_vars), dry_run=dry_run, check=check)


def spack_user_cfg_dir(spack_root: str, *, dry_run: bool, sandbox: Path | None = None) -> Path:
    # Spack >=1.2: print-file gives us the exact file path.
    r = spack_run(spack_root, ["config", "--scope", "user", "edit", "--print-file", "config"], dry_run=dry_run, check=False, sandbox=sandbox)
    if dry_run:
        # Best guess; used only for printing in dry-run mode
        return (sandbox / ".spack") if sandbox else (Path.home() / ".spack")
    if r.returncode != 0:
        raise SystemExit("ERROR: couldn't determine user config dir (spack config edit --print-file config failed)")
    path = r.stdout.strip().splitlines()[-1].strip()
    if not path:
        raise SystemExit("ERROR: spack didn't print a config file path for user scope")
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.parent


def ensure_spack(spack_root: str, spack_repo: str, *, dry_run: bool, sandbox: Path | None = None) -> None:
    git_clone_if_missing(f"git@github.com:{spack_repo}.git", Path(spack_root), dry_run=dry_run)
    r = spack_run(spack_root, ["--version"], dry_run=dry_run, check=False, sandbox=sandbox)
    if dry_run:
        eprint("==> Spack available: spack")
    else:
        eprint("==> Spack available: spack")
        if r.stdout.strip():
            eprint(r.stdout.strip())


def ensure_repos(spack_root: str, spack_packages_dir: str, spack_packages_repo: str, *, dry_run: bool, sandbox: Path | None = None) -> None:
    git_clone_if_missing(f"git@github.com:{spack_packages_repo}.git", Path(spack_packages_dir), dry_run=dry_run)

    base = sandbox if sandbox else Path.home()
    geosesm_dir = base / "geosesm-spack"
    git_clone_if_missing("git@github.com:GMAO-SI-Team/geosesm-spack.git", geosesm_dir, dry_run=dry_run)

    user_cfg = spack_user_cfg_dir(spack_root, dry_run=dry_run, sandbox=sandbox)
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

    for name in ["repos.yaml", "packages.yaml", "concretizer.yaml", "config.yaml", "compilers.yaml"]:
        src = user_cfg / name
        if src.exists():
            if dry_run:
                eprint(f"[dry-run] cp -a {src} {bdir/name}")
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


def ensure_tcsh_external_via_spack_python(spack_root: str, *, dry_run: bool, brew_prefix: str, sandbox: Path | None = None) -> None:
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
            raise SystemExit(f"ERROR: spack python failed while ensuring tcsh external (exit {proc.returncode})")

        if proc.stdout.strip():
            eprint(proc.stdout.strip())

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def ensure_config(spack_root: str, *, dry_run: bool, sandbox: Path | None = None) -> None:
    eprint("==> Setting build_jobs=6")
    spack_run(spack_root, ["config", "--scope", "user", "add", "config:build_jobs:6"], dry_run=dry_run, check=False, sandbox=sandbox)

    base = sandbox if sandbox else Path.home()
    env_root = str(base / "spack-envs")
    eprint(f"==> Setting environments_root={env_root}")
    spack_run(spack_root, ["config", "--scope", "user", "add", f"config:environments_root:{env_root}"], dry_run=dry_run, check=False, sandbox=sandbox)

    eprint("==> Finding compilers")
    spack_run(spack_root, ["compiler", "find"], dry_run=dry_run, check=False, sandbox=sandbox)

    eprint("==> Finding externals (with excludes) + bash")
    ext_cmd = ["external", "find"]
    for ex in EXTERNAL_FIND_EXCLUDES:
        ext_cmd.extend(["--exclude", ex])
    spack_run(spack_root, ext_cmd, dry_run=dry_run, check=False, sandbox=sandbox)
    spack_run(spack_root, ["external", "find", "bash"], dry_run=dry_run, check=False, sandbox=sandbox)

    # Ensure tcsh external using spack python rather than config add
    if is_mac_os():
        brew = shutil_which("brew") or str(Path.home() / ".homebrew" / "bin" / "brew")
        brew_prefix = "/opt/homebrew"
        if not dry_run:
            r = run([brew, "--prefix"], dry_run=False, check=False)
            if r.returncode == 0 and r.stdout.strip():
                brew_prefix = r.stdout.strip()
        ensure_tcsh_external_via_spack_python(spack_root, dry_run=dry_run, brew_prefix=brew_prefix, sandbox=sandbox)

    user_cfg = spack_user_cfg_dir(spack_root, dry_run=dry_run, sandbox=sandbox)
    concretizer_yaml = user_cfg / "concretizer.yaml"
    eprint(f"==> Writing concretizer.yaml (reuse: false) -> {concretizer_yaml}")
    content = "concretizer:\n  reuse: false\n"
    if dry_run:
        eprint("[dry-run] would write:\n" + content.rstrip())
    else:
        concretizer_yaml.write_text(content)




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
        if '@' in comp:
            name, ver = comp.split('@', 1)
            # Simplify version to major version for common compilers
            ver_parts = ver.split('.')
            if name in ('gcc', 'gfortran', 'nag'):
                ver_short = ver_parts[0]
            elif name in ('apple-clang', 'clang'):
                ver_short = ver_parts[0]
            else:
                ver_short = ver_parts[0]
            parts.append(f"{name.replace('-', '')}{ver_short}")
        else:
            # No version specified, just use compiler name
            parts.append(comp.replace('-', ''))

    if python:
        # Extract Python version from specs like "3.12", "@3.11", "3.10.2"
        py = python.strip().lstrip('@')
        py_parts = py.split('.')
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
    sandbox: Path | None = None,
    custom_spec: str | None = None,
) -> None:
    if env_dir.exists():
        eprint(f"==> Environment already exists: {env_dir}")
        return

    eprint(f"==> Creating environment: {env_name}")

    # Validate apple-clang usage
    if compiler and 'apple-clang' in compiler and not (compiler_c or compiler_fortran):
        raise SystemExit(
            f"ERROR: apple-clang does not include a Fortran compiler.\n"
            f"For Fortran support, use one of these options:\n"
            f"  1. --compiler gcc@15 (recommended: auto-uses apple-clang for C/C++, gcc for Fortran)\n"
            f"  2. --compiler-c apple-clang@17 --compiler-fortran gcc@15 (explicit control)\n"
            f"  3. --compiler-fortran gcc@15 (uses default apple-clang for C/C++, gcc for Fortran)"
        )

    # Determine compiler strategy
    # On macOS, if user specifies gcc, use apple-clang for C/C++ and gcc for Fortran (best practice)
    # unless explicit overrides are provided
    c_spec = None
    fortran_spec = None
    
    if compiler_c or compiler_fortran:
        # Explicit overrides - validate apple-clang + fortran combination
        if compiler_fortran and 'apple-clang' in compiler_fortran:
            raise SystemExit(
                f"ERROR: apple-clang does not include a Fortran compiler.\n"
                f"Use gcc, gfortran, or nag for --compiler-fortran."
            )
        c_spec = compiler_c
        fortran_spec = compiler_fortran
    elif compiler:
        # Smart defaults based on compiler choice
        if is_mac_os() and compiler.startswith(('gcc', 'gfortran')):
            # macOS + gcc: use apple-clang for C/C++, gcc for Fortran
            # Find the default apple-clang version
            c_spec = 'apple-clang'  # Spack will find the default version
            fortran_spec = compiler
            eprint(f"==> macOS detected: using apple-clang for C/C++, {compiler} for Fortran")
        else:
            # Use specified compiler for all languages
            c_spec = compiler
            fortran_spec = compiler

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
    # Build compiler constraint suffix if needed (propagates to all dependencies)
    compiler_suffix = ""
    if c_spec and fortran_spec:
        # Both C/C++ and Fortran specified
        if is_mac_os() and fortran_spec.startswith('gcc'):
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
        if is_mac_os() and fortran_spec.startswith('gcc'):
            # macOS with gcc: explicitly set apple-clang for C/C++, gcc for Fortran
            compiler_suffix = f" %[when='%c'] c=apple-clang %[when='%cxx'] cxx=apple-clang %[when='%fortran'] fortran={fortran_spec}"
        else:
            compiler_suffix = f" %{fortran_spec} languages:=fortran"
    
    if custom_spec:
        spec_lines = [f"    - {custom_spec}{compiler_suffix}"]
        eprint(f"==> Using custom spec '{custom_spec}' (for 'spack install --only dependencies' workflow)")
    else:
        # Default: use geosgcm
        spec_lines = [f"    - {DEFAULT_SPEC}{compiler_suffix}"]
        eprint(f"==> Using default spec '{DEFAULT_SPEC}'")
    specs = "\n".join(spec_lines)

    packages_block = ""
    if python:
        lines = ["  packages:"]
        # Ensure Python version has @ prefix for proper spec format
        py_spec = python.strip()
        if not py_spec.startswith('@'):
            py_spec = f"@{py_spec}"
        lines.append("    python:")
        lines.append(f"      require: '{py_spec}'")
        packages_block = "\n" + "\n".join(lines) + "\n"

    # Add compiler env vars to work around Spack bug #51855
    # This helps Spack find the correct compilers in various workflows
    env_vars_block = ""
    if has_compiler_constraint:
        compiler_paths = find_system_compiler_paths(c_spec, fortran_spec)
        if compiler_paths:
            eprint("==> Adding compiler env vars (workaround for Spack bug #51855):")
            lines = ["  env_vars:"]
            lines.append("    set:")
            for var, path in sorted(compiler_paths.items()):
                lines.append(f"      {var}: {path}")
                eprint(f"    {var}={path}")
            env_vars_block = "\n" + "\n".join(lines) + "\n"

    content = f"""spack:
  specs:
{specs}
  concretizer:
    unify: {unify_val}
{packages_block}{env_vars_block}  view: false
"""

    spack_yaml = env_dir / "spack.yaml"

    if dry_run:
        eprint(f"[dry-run] would create directory {env_dir}")
        eprint(f"[dry-run] would write {spack_yaml}")
        eprint(f"[dry-run]   C/C++ compiler: {c_spec or 'default'}")
        eprint(f"[dry-run]   Fortran compiler: {fortran_spec or 'default'}")
        eprint(f"[dry-run]   Python: {python or 'default'}")
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
  
  %(prog)s --spack fork --fork jcsda setup
      Set up a custom fork (jcsda/spack and jcsda/spack-packages)
  
  %(prog)s --dry-run --spack official config-clean
      Preview what config-clean would do without making changes

For more details, see the docstring at the top of this script.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True
    )
    p.add_argument("--dry-run", action="store_true", help="Print actions without changing anything")
    p.add_argument("--sandbox", type=Path, default=None,
                   help="Install everything in this directory (for testing/isolation). "
                        "Repos, config, and environments will all go under this path.")

    # Spack selection
    p.add_argument("--spack", choices=["interactive", "official", "mathomp4", "fork"], default="interactive",
                   help="Which Spack to use (default: interactive, prompts user)")
    p.add_argument("--fork", default=None, help="Fork org/user name (required with --spack fork)")
    p.add_argument("--spack-repo", default=None, help="Override spack repo (e.g., org/spack)")
    p.add_argument("--spack-packages-repo", default=None, help="Override spack-packages repo (e.g., org/spack-packages)")


    sub = p.add_subparsers(dest="cmd", required=False)

    # Simple commands with no extra args
    sub.add_parser("all", help="Full bootstrap: brew + spack + repos + config + default env")
    sub.add_parser("brew", help="Install Homebrew prerequisites only")
    sub.add_parser("spack", help="Clone Spack repository only")
    sub.add_parser("repos", help="Clone and configure spack-packages and geosesm-spack repos")
    sub.add_parser("config", help="Configure Spack (build_jobs, compilers, externals, concretizer)")
    sub.add_parser("setup", help="Complete setup without environment: brew + spack + repos + config")
    sub.add_parser("env", help="Create default 'geos' environment")
    sub.add_parser("reset", help="Backup and remove user-scope config files")
    sub.add_parser("config-clean", help="Reset config, then rebuild repos and config from scratch")

    # env-create: create a named environment (optionally with compiler constraint)
    p_envc = sub.add_parser("env-create", 
        help="Create a custom Spack environment with optional compiler/Python constraints",
        description="""
Create a managed Spack environment under ~/spack-envs with optional toolchain constraints.
Use --auto-name to generate environment names from specs (e.g., geos-gcc15-py312).

On macOS, when --compiler gcc@X is specified, the script automatically uses apple-clang
for C/C++ and gcc for Fortran (best practice). Use --compiler-c and --compiler-fortran
for explicit control.
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
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p_envc.add_argument("--name", default="geos", help="Environment name (default: geos)")
    p_envc.add_argument("--auto-name", action="store_true", 
                        help="Auto-generate name from compiler/python specs (e.g., geos-gcc15-py312)")
    p_envc.add_argument("--compiler", default=None, 
                        help="Compiler constraint (e.g., gcc@15, apple-clang@17). "
                             "On macOS, gcc@X uses apple-clang for C/C++ and gcc for Fortran.")
    p_envc.add_argument("--compiler-c", default=None,
                        help="Explicit C/C++ compiler (overrides --compiler for C/C++)")
    p_envc.add_argument("--compiler-fortran", default=None,
                        help="Explicit Fortran compiler (overrides --compiler for Fortran)")
    p_envc.add_argument("--python", default=None, 
                        help="Python version constraint (e.g., 3.12, @3.11, 3.10.2)")
    p_envc.add_argument("--spec", default=None,
                        help="Use a custom spec (e.g., 'geosgcm', 'mapl') instead of individual packages. "
                             "Intended for 'spack install --only dependencies' workflow. "
                             "Automatically adds CC/CXX/FC env vars (Spack bug #51855 workaround).")

    p.set_defaults(cmd="all")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
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

    if cmd in ("all", "brew", "setup"):
        if is_mac_os():
            ensure_brew_prereqs(brew, dry_run=dry_run)
        if cmd == "brew":
            return 0

    if cmd in ("all", "spack", "repos", "config", "setup", "env", "reset", "config-clean", "env-create"):
        ensure_spack(spack_root, layout["spack_repo"], dry_run=dry_run, sandbox=sandbox)

    if cmd == "spack":
        return 0

    if cmd in ("reset", "config-clean"):
        reset_user_cfg(spack_root, dry_run=dry_run, sandbox=sandbox)
        if cmd == "reset":
            return 0

    if cmd in ("all", "repos", "setup", "config-clean", "env-create"):
        ensure_repos(spack_root, spack_packages_dir, layout["spack_packages_repo"], dry_run=dry_run, sandbox=sandbox)
        if cmd == "repos":
            return 0

    if cmd in ("all", "config", "setup", "config-clean", "env-create"):
        ensure_config(spack_root, dry_run=dry_run, sandbox=sandbox)
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
        if cmd == "env-create":
            compiler = getattr(args, "compiler", None)
            compiler_c = getattr(args, "compiler_c", None)
            compiler_fortran = getattr(args, "compiler_fortran", None)
            python = getattr(args, "python", None)
            custom_spec = getattr(args, "spec", None)
            auto_name = getattr(args, "auto_name", False)
            if auto_name:
                # For auto-naming, use the effective compiler (for fortran if split)
                name_compiler = compiler_fortran or compiler
                env_name = auto_env_name("geos", name_compiler, python)
            else:
                env_name = getattr(args, "name", "geos")
        else:
            custom_spec = None
        env_path = env_root / env_name

        create_env(spack_root, env_path, dry_run=dry_run, env_name=env_name, 
                   compiler=compiler, compiler_c=compiler_c, compiler_fortran=compiler_fortran,
                   python=python, sandbox=sandbox, custom_spec=custom_spec)

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
