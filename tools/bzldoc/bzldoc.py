#!/usr/bin/python3
"""Extracts metadata from a bzl file.

This script parses a .bzl file and produces a data structure representation
suitable for rendering into documentation.
"""

# standard libraries
import ast
import os.path
import sys
import textwrap
import typing

# third party libraries
import yaml
from absl import app, flags, logging

from tools.bzldoc.bzl2yaml_lib import BzlDoc
from tools.codegen.codegen_lib import Template
from tools.mdfmt.mdfmt_lib import format_string

FLAGS = flags.FLAGS
flags.DEFINE_string("input", None, "bzl file to parse")
flags.DEFINE_string("short_path", None, 'bazel "short path" to bzl file')
flags.DEFINE_string("template", None, "path to markdown template")
flags.DEFINE_string("output", None, "markdown file to write")


def main(argv):
    if len(argv) != 1:
        logging.fatal("Unsupported command line arguments: %r", argv[1:])

    bzldoc = BzlDoc()
    bzldoc.ParseFile(FLAGS.input, FLAGS.short_path)

    t = Template()
    t.LoadTemplate(FLAGS.template)
    t.MergeData(bzldoc.Data())
    output = t.Render()
    output = format_string(output)

    if FLAGS.output:
        with open(FLAGS.output, "w", encoding="utf-8") as fd:
            fd.write(output)
            fd.close()
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    app.run(main)
