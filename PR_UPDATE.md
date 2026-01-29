# PR Update: Major Improvements to Compiler Handling and Default Workflow

## Summary

This update significantly improves the default workflow and compiler constraint handling, making the tool much more aligned with the primary use case: installing dependencies of GEOSgcm or other packages for local development.

## Key Changes

### 1. Default Spec Changed to `geosgcm`

**Before:** Environments contained a list of individual packages (python, py-numpy, openmpi, esmf, gftl, pfunit, etc.)

**After:** Environments default to a single spec: `geosgcm`, optimized for the dependency-only workflow.

**Benefits:**
- Aligns with the primary use case: `spack install --only dependencies`
- Cleaner, more maintainable environment definitions
- Users can still specify alternative packages with `--spec mapl`, `--spec esmf`, etc.

**Example:**
```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15
# Creates environment for geosgcm dependencies (default)

./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --spec mapl
# Creates environment for mapl dependencies
```

### 2. Spec-Based Compiler Constraints

**Before:** Compiler constraints were applied via `packages:all:require` in spack.yaml

**After:** Compiler constraints are applied directly to the spec using Spack's conditional syntax

**Generated spack.yaml (before):**
```yaml
specs:
  - python
  - py-numpy
  - openmpi
  ...
packages:
  all:
    require: ['%apple-clang languages:=c,cxx', '%gcc@15 languages:=fortran']
```

**Generated spack.yaml (after):**
```yaml
specs:
  - geosgcm %[when='%c'] c=apple-clang %[when='%cxx'] cxx=apple-clang %[when='%fortran'] fortran=gcc@15
```

**Benefits:**
- Compiler constraints propagate naturally to all dependencies
- More idiomatic Spack syntax
- Better control and clarity
- Avoids edge cases with Python trying to compile with gcc on macOS

### 3. Improved macOS Compiler Handling

**Smart defaults now properly implemented:**

When you specify `--compiler gcc@15` on macOS:
- C/C++ compilation: Uses `apple-clang` (optimal for Apple Silicon)
- Fortran compilation: Uses `gcc@15` (better Fortran support)
- **Correctly applies to all dependencies**

**Previous issue:** Dependencies like cmake, curl, py-numpy were incorrectly using `%c,cxx=gcc` instead of `%c,cxx=apple-clang`

**Fixed:** Now all C/C++ dependencies correctly use apple-clang, and Fortran packages correctly use gcc

**Example concretization output:**
```
geosgcm@11.8.1 %c,cxx=apple-clang@17.0.0 %fortran=gcc@15.2.0
  ^cmake@3.31.9 %c,cxx=apple-clang@17.0.0
  ^openmpi@5.0.9 %c,cxx=apple-clang@17.0.0 %fortran=gcc@15.2.0
  ^esmf@8.9.1 %c,cxx=apple-clang@17.0.0 %fortran=gcc@15.2.0
```

### 4. Compiler Environment Variables Applied Universally

**Before:** Compiler env vars (CC, CXX, FC) were only added when using `--spec`

**After:** Compiler env vars are added whenever compiler constraints are specified

**Benefit:** Works around Spack bug #51855 for all environments, not just custom specs

### 5. Clear User Communication

**Added output message showing which spec is being used:**

```
This environment will install dependencies of: geosgcm
```

**Updated final instructions:**

Before:
```
Next steps:
  spack install
```

After:
```
This environment will install dependencies of: geosgcm

Next steps:
  spack env activate geos-gcc15
  spack concretize
  spack install --only dependencies
```

**Benefit:** Users immediately understand the workflow and which package dependencies they're installing

### 6. Python Constraint Fixes

**Issue:** When specifying `--compiler gcc@15`, Python was attempting to compile with gcc on macOS, which fails with:
```
ERROR: CPython does not compile with GCC on macOS yet, use clang
```

**Solution:** 
- Spec-based constraints with conditional compilation naturally handle this
- Python and all py-* packages correctly use apple-clang for C/C++
- Fortran packages (esmf, openmpi, gftl, etc.) correctly use gcc for Fortran

## Usage Examples

### Basic usage (unchanged):
```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15
```

Now creates environment with `geosgcm` spec and proper compiler constraints.

### Alternative package:
```bash
./bootstrap_spack.py --spack mathomp4 env-create --auto-name --compiler gcc@15 --spec mapl
```

### Then install dependencies:
```bash
source ~/spack-mathomp4/share/spack/setup-env.sh
spack env activate geos-gcc15
spack concretize
spack install --only dependencies

cd ~/GEOSgcm  # or ~/MAPL
make install
```

## Technical Details

### Compiler Constraint Syntax

The new spec-based approach uses Spack's conditional compilation syntax:

```yaml
%[when='%c'] c=apple-clang         # Use apple-clang for C
%[when='%cxx'] cxx=apple-clang     # Use apple-clang for C++
%[when='%fortran'] fortran=gcc@15  # Use gcc@15 for Fortran
```

This is applied to the root spec and automatically propagates to all dependencies.

### Code Changes

1. **Removed `STARTER_ENV_SPECS` constant**, replaced with `DEFAULT_SPEC = "geosgcm"`
2. **Updated `create_env()` to build compiler constraint suffix** and append to specs
3. **Fixed smart macOS handling** to explicitly set `c_spec = 'apple-clang'` instead of `None`
4. **Simplified packages block** to only handle Python version constraints
5. **Updated `print_minimal_advice()`** to show which spec is being used
6. **Updated all documentation** in README.md

## Breaking Changes

**None.** All existing workflows continue to work:
- `--spec` flag works the same (now optional since geosgcm is default)
- All compiler flags work the same
- Sandbox mode unchanged
- Auto-naming unchanged

The only difference is the default environment now uses `geosgcm` instead of a package list, which is actually more aligned with the intended usage.

## Testing

Tested on macOS (Apple Silicon) with:
- `--compiler gcc@15` (default smart handling)
- `--compiler gcc@15 --python 3.12` (with Python constraint)
- `--spec mapl` (alternative package)
- `--compiler-c apple-clang@17 --compiler-fortran gcc@15` (explicit control)

All test cases successfully concretize and show correct compiler assignments for C/C++ (apple-clang) and Fortran (gcc).

## Documentation Updates

- Updated README.md comprehensively to reflect new default behavior
- Clarified that geosgcm is the default spec
- Updated all examples to use `spack install --only dependencies`
- Added clear explanation of spec-based compiler constraints
- Updated troubleshooting section

## Future Enhancements

Potential future improvements:
- Support for multiple specs in one environment
- Pre-built spec templates (geosgcm-full, mapl-minimal, etc.)
- Integration with mepo for automatic repo management
