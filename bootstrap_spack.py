#!/usr/bin/env python3
"""
bootstrap_spack.py

macOS-focused bootstrap for Spack >= 1.2 with fork-aware layout:

- official:  ~/spack            and ~/spack-packages
- fork:      ~/spack-<fork>     and ~/spack-packages-<fork>

What it does (by subcommand):
- brew          : verify Homebrew, install prereqs
- spack         : clone chosen Spack
- repos         : clone matching spack-packages + geosesm-spack, write repos.yaml
- config        : build_jobs, compiler find, external find (with excludes), ensure tcsh external, concretizer.yaml
- env           : create starter spack environment
- reset         : backup + remove user-scope repos.yaml/packages.yaml/concretizer.yaml
- config-clean  : reset + repos + config
- all           : brew + spack + repos + config + env

Key design choice:
- For 'tcsh' external we do NOT use `spack config add ...` with an array-of-dicts on the CLI
  (it is finicky to encode). Instead we use `spack python` and Spack's internal config API to
  update the user-scope packages configuration safely and idempotently.

Usage examples:
  ./bootstrap_spack.py all
  ./bootstrap_spack.py --dry-run config-clean
  ./bootstrap_spack.py --spack official config
  ./bootstrap_spack.py --spack fork --fork mathomp4 all
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

STARTER_ENV_SPECS = [
    "python",
    "py-numpy",
    "py-pyyaml",
    "py-ruamel-yaml",
    "openmpi",
    "esmf",
    "gftl",
    "gftl-shared",
    "fargparse",
    "pfunit",
    "pflogger",
    "yafyaml",
    "mepo",
    "udunits",
]


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)



def print_minimal_advice(spack_root: str, env_name: str | None = None) -> None:
    eprint("")
    eprint("=" * 64)
    eprint("Spack bootstrap complete.")
    eprint("")
    eprint("To enable Spack in this shell:")
    eprint(f'  source "{spack_root}/share/spack/setup-env.sh"')
    eprint("")
    if env_name:
        eprint("Next steps:")
        eprint("  spack env list")
        eprint(f"  spack env activate {env_name}")
        eprint("  spack concretize")
        eprint("  spack install")
        eprint("")
    eprint("=" * 64)
    eprint("")


plateforms = {
    "MacOS": "darwin",
    "Linux": "linux",
}


def is_supported_platform() -> bool:
    return sys.platform in list(plateforms.values())


def is_mac_os() -> bool:
    return sys.platform == plateforms["MacOS"]


def is_linux() -> bool:
    return sys.platform == plateforms["Linux"]


def run(cmd: Sequence[str], *, dry_run: bool, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    if dry_run:
        eprint("[dry-run]", " ".join(shlex.quote(c) for c in cmd))
        # Fake a CompletedProcess
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return subprocess.run(cmd, check=check, text=True, capture_output=True, env=env)


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


def spack_layout(spack_choice: str, fork: str | None, spack_repo: str | None, spack_packages_repo: str | None) -> dict:
    """
    Return dict with:
      fork_slug, spack_repo, spack_packages_repo, spack_root, spack_packages_dir
    """
    home = Path.home()

    if spack_choice == "official":
        return dict(
            fork_slug="official",
            spack_repo="spack/spack",
            spack_packages_repo="spack/spack-packages",
            spack_root=str(home / "spack"),
            spack_packages_dir=str(home / "spack-packages"),
        )

    if spack_choice == "mathomp4":
        fork_slug = "mathomp4"
        return dict(
            fork_slug=fork_slug,
            spack_repo="mathomp4/spack",
            spack_packages_repo="mathomp4/spack-packages",
            spack_root=str(home / f"spack-{fork_slug}"),
            spack_packages_dir=str(home / f"spack-packages-{fork_slug}"),
        )

    # fork
    if not fork:
        raise SystemExit("ERROR: --fork is required when --spack fork")
    fork_slug = slugify(fork)
    return dict(
        fork_slug=fork_slug,
        spack_repo=spack_repo or f"{fork_slug}/spack",
        spack_packages_repo=spack_packages_repo or f"{fork_slug}/spack-packages",
        spack_root=str(home / f"spack-{fork_slug}"),
        spack_packages_dir=str(home / f"spack-packages-{fork_slug}"),
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


def spack_bash_prefix(spack_root: str) -> str:
    # Using bash -lc so we can source setup-env.sh.
    return f"source {shlex.quote(spack_root)}/share/spack/setup-env.sh"


def spack_cmd(spack_root: str, args: Sequence[str]) -> str:
    # Return a bash -lc string that runs spack with given args.
    return f"{spack_bash_prefix(spack_root)} && spack {' '.join(shlex.quote(a) for a in args)}"


def spack_run(spack_root: str, args: Sequence[str], *, dry_run: bool, check: bool = True) -> subprocess.CompletedProcess:
    return run_bash(spack_cmd(spack_root, args), dry_run=dry_run, check=check)


def spack_user_cfg_dir(spack_root: str, *, dry_run: bool) -> Path:
    # Spack >=1.2: print-file gives us the exact file path.
    r = spack_run(spack_root, ["config", "--scope", "user", "edit", "--print-file", "config"], dry_run=dry_run, check=False)
    if dry_run:
        # Best guess; used only for printing in dry-run mode
        return Path.home() / ".spack"
    if r.returncode != 0:
        raise SystemExit("ERROR: couldn't determine user config dir (spack config edit --print-file config failed)")
    path = r.stdout.strip().splitlines()[-1].strip()
    if not path:
        raise SystemExit("ERROR: spack didn't print a config file path for user scope")
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.parent


def ensure_spack(spack_root: str, spack_repo: str, *, dry_run: bool) -> None:
    git_clone_if_missing(f"git@github.com:{spack_repo}.git", Path(spack_root), dry_run=dry_run)
    r = spack_run(spack_root, ["--version"], dry_run=dry_run, check=False)
    if dry_run:
        eprint("==> Spack available: spack")
    else:
        eprint("==> Spack available: spack")
        if r.stdout.strip():
            eprint(r.stdout.strip())


def ensure_repos(spack_root: str, spack_packages_dir: str, spack_packages_repo: str, *, dry_run: bool) -> None:
    git_clone_if_missing(f"git@github.com:{spack_packages_repo}.git", Path(spack_packages_dir), dry_run=dry_run)

    geosesm_dir = Path.home() / "geosesm-spack"
    git_clone_if_missing("git@github.com:GMAO-SI-Team/geosesm-spack.git", geosesm_dir, dry_run=dry_run)

    user_cfg = spack_user_cfg_dir(spack_root, dry_run=dry_run)
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


def reset_user_cfg(spack_root: str, *, dry_run: bool) -> None:
    user_cfg = spack_user_cfg_dir(spack_root, dry_run=dry_run)
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


def ensure_tcsh_external_via_spack_python(spack_root: str, *, dry_run: bool, brew_prefix: str) -> None:
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

        proc = run_bash(
            f"{spack_bash_prefix(spack_root)} && spack python {shlex.quote(tmp_path)}",
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


def ensure_config(spack_root: str, *, dry_run: bool) -> None:
    eprint("==> Setting build_jobs=6")
    spack_run(spack_root, ["config", "--scope", "user", "add", "config:build_jobs:6"], dry_run=dry_run, check=False)

    env_root = str(Path.home() / "spack-envs")
    eprint(f"==> Setting environments_root={env_root}")
    spack_run(spack_root, ["config", "--scope", "user", "add", f"config:environments_root:{env_root}"], dry_run=dry_run, check=False)

    eprint("==> Finding compilers")
    spack_run(spack_root, ["compiler", "find"], dry_run=dry_run, check=False)

    eprint("==> Finding externals (with excludes) + bash")
    ext_cmd = ["external", "find"]
    for ex in EXTERNAL_FIND_EXCLUDES:
        ext_cmd.extend(["--exclude", ex])
    spack_run(spack_root, ext_cmd, dry_run=dry_run, check=False)
    spack_run(spack_root, ["external", "find", "bash"], dry_run=dry_run, check=False)

    # Ensure tcsh external using spack python rather than config add
    if is_mac_os():
        brew = shutil_which("brew") or str(Path.home() / ".homebrew" / "bin" / "brew")
        brew_prefix = "/opt/homebrew"
        if not dry_run:
            r = run([brew, "--prefix"], dry_run=False, check=False)
            if r.returncode == 0 and r.stdout.strip():
                brew_prefix = r.stdout.strip()
        ensure_tcsh_external_via_spack_python(spack_root, dry_run=dry_run, brew_prefix=brew_prefix)

    user_cfg = spack_user_cfg_dir(spack_root, dry_run=dry_run)
    concretizer_yaml = user_cfg / "concretizer.yaml"
    eprint(f"==> Writing concretizer.yaml (reuse: false) -> {concretizer_yaml}")
    content = "concretizer:\n  reuse: false\n"
    if dry_run:
        eprint("[dry-run] would write:\n" + content.rstrip())
    else:
        concretizer_yaml.write_text(content)




def create_env(
    spack_root: str,
    env_dir: Path,
    *,
    dry_run: bool,
    env_name: str = "geos",
    compiler: str | None = None,
) -> None:
    if env_dir.exists():
        eprint(f"==> Environment already exists: {env_dir}")
        return

    eprint(f"==> Creating environment: {env_name}")

    # Concretizer policy:
    # - no compiler pin  -> unify: true
    # - compiler pinned -> unify: when_possible (macOS tends to need this)
    unify_val = "when_possible" if compiler else "true"

    # Specs (do NOT append %compiler to each spec; instead pin via packages:all:compiler)
    spec_lines = [f"    - {s}" for s in STARTER_ENV_SPECS]
    specs = "\n".join(spec_lines)

    packages_block = ""
    if compiler:
        packages_block = f"""
  packages:
    all:
      compiler: [{compiler}]
"""

    content = f"""spack:
  specs:
{specs}
  concretizer:
    unify: {unify_val}
{packages_block}  view: false
"""

    spack_yaml = env_dir / "spack.yaml"

    if dry_run:
        eprint(f"[dry-run] would create directory {env_dir}")
        eprint(f"[dry-run] would write {spack_yaml} with compiler pin = {compiler!r}")
        return

    env_dir.mkdir(parents=True, exist_ok=True)
    spack_yaml.write_text(content)
    eprint(f"==> Wrote {spack_yaml}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--dry-run", action="store_true", help="Print actions without changing anything")

    # Spack selection
    p.add_argument("--spack", choices=["interactive", "official", "mathomp4", "fork"], default="interactive",
                   help="Which Spack to use")
    p.add_argument("--fork", default=None, help="Fork org/user (used with --spack fork)")
    p.add_argument("--spack-repo", default=None, help="Override spack repo (e.g., org/spack)")
    p.add_argument("--spack-packages-repo", default=None, help="Override spack-packages repo (e.g., org/spack-packages)")

    
    sub = p.add_subparsers(dest="cmd", required=False)

    # Simple commands with no extra args
    for name in ["all", "brew", "spack", "repos", "config", "env", "reset", "config-clean"]:
        sub.add_parser(name)

    # env-create: create a named environment (optionally with compiler constraint)
    p_envc = sub.add_parser("env-create", help="Create a managed Spack environment under environments_root")
    p_envc.add_argument("--name", default="geos", help="Environment name (default: geos)")
    p_envc.add_argument("--compiler", default=None, help="Compiler constraint (e.g., gcc@15, apple-clang@17, nag)")

    p.set_defaults(cmd="all")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not is_supported_platform():
        eprint(f"ERROR: this bootstrap currently targets {list(plateforms.values())}.")
        return 2

    dry_run = bool(args.dry_run)

    # Brew is always useful early
    if is_mac_os():
        brew = ensure_homebrew(dry_run=dry_run)

    # Determine spack choice
    spack_choice = args.spack
    fork = args.fork
    if spack_choice == "interactive":
        spack_choice, fork = pick_spack_interactive()

    layout = spack_layout(spack_choice, fork, args.spack_repo, args.spack_packages_repo)
    spack_root = layout["spack_root"]
    spack_packages_dir = layout["spack_packages_dir"]

    eprint("==> Selected:")
    eprint(f"  SPACK_REPO          = {layout['spack_repo']}")
    eprint(f"  SPACK_ROOT          = {spack_root}")
    eprint(f"  SPACKPACKAGES_REPO  = {layout['spack_packages_repo']}")
    eprint(f"  SPACKPACKAGES_DIR   = {spack_packages_dir}")

    cmd = args.cmd

    if cmd in ("all", "brew"):
        if is_mac_os():
            ensure_brew_prereqs(brew, dry_run=dry_run)
        if cmd == "brew":
            return 0

    if cmd in ("all", "spack", "repos", "config", "env", "reset", "config-clean"):
        ensure_spack(spack_root, layout["spack_repo"], dry_run=dry_run)

    if cmd == "spack":
        return 0

    if cmd in ("reset", "config-clean"):
        reset_user_cfg(spack_root, dry_run=dry_run)
        if cmd == "reset":
            return 0

    if cmd in ("all", "repos", "config-clean"):
        ensure_repos(spack_root, spack_packages_dir, layout["spack_packages_repo"], dry_run=dry_run)
        if cmd == "repos":
            return 0

    if cmd in ("all", "config", "config-clean"):
        ensure_config(spack_root, dry_run=dry_run)
        if cmd == "config":
            return 0

    
    if cmd in ("all", "env", "env-create"):
        # Managed environments live under environments_root (we set this to ~/spack-envs).
        env_root = Path.home() / "spack-envs"
        env_name = "geos"
        compiler = None
        if cmd == "env-create":
            env_name = getattr(args, "name", "geos")
            compiler = getattr(args, "compiler", None)
        env_path = env_root / env_name

        create_env(spack_root, env_path, dry_run=dry_run, env_name=env_name, compiler=compiler)

        if cmd == "env-create":
            print_minimal_advice(spack_root, env_name)
            return 0

        if cmd == "env":
            return 0

    # If we reached here via all or config-clean, print minimal guidance.
    if cmd in ("all", "config-clean"):
        # Only mention env steps if the default env exists
        env_path = Path.home() / "spack-envs" / "geos"
        if env_path.exists() and (env_path / "spack.yaml").exists():
            print_minimal_advice(spack_root, "geos")
        else:
            print_minimal_advice(spack_root, None)

    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
