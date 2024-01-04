#!/bin/sh
#
# This is a smoke-test for fix_format that runs fix_format over a
# representative set of files.
#
# TODO(jonathan): make this into a bazel sh_test.

set -e

cd $(git rev-parse --show-toplevel)

# run fix_format with one of each type of file
EXAMPLE_FILES=(
  README.md
  bazel/eda/examples/verilog/sub0.sv
  bazel/py.bzl
  guidelines/cpp/example/lib/base.cc
  guidelines/cpp/example/lib/base.h
  guidelines/python/example/foo.py
  guidelines/rust/example/src/lib.rs
  bogus/file.cc
)

bazel run //tools/fix_format -- --v=0 "${EXAMPLE_FILES[@]}"

bazel build //tools/fix_format:fix_format_zip
cp -f ./bazel-bin/tools/fix_format/fix_format.zip /tmp/fix_format.zip
time python3 /tmp/fix_format.zip --v=0 "${EXAMPLE_FILES[@]}"

