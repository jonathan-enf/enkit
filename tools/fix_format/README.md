# fix_format

One-stop shop for all code reformatting at Enfabrica.

## Caching in astore

Because the `fix_format` tools has many external dependencies that take a long
time to build, the compiled pyzip "binary" is cached in astore. The typical
way to run this tool is using the `fix_format.sh` wrapper script, which in
turn invokes the cached `@net_enfabrica_binary_fix_format//:fix_format_pyzip`
file.

### Deploying

To deploy an updated version of `fix_format`:

1. Build: `bazel build :fix_format_pyzip`

1. Push build to astore: `bazel run :deploy`

1. Update `fix_format.version.bzl` to select the version you just deployed.
   `sha256sum` can be used to calculate `FIX_FORMAT_DIGEST`, and
   `enkit astore ls -l tools/fix_format_pyzip` can show you the value to use
   for `FIX_FORMAT_UID`.

### Testing

If you want to run this target directly for testing, you can try:

```
bazel run "@net_enfabrica_binary_fix_format//:fix_format_pyzip" \
   -- "$(pwd)/file1" "$(pwd)/file2" etc...
```

If you want to build everything from source, `fix_format.sh --build` will do
so.
