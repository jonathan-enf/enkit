#!/usr/bin/python3
#
# Render a jinja2 template using a variety of input data formats.
#
# Data inputs are parsed in the order they are specified on the commandline.
# Data is merged in the following ways:
#   dictionaries are merged, recursively.
#   lists are appended to each other.
#   scalars override the previous value.
#
# Currently, we are using jinja2 version 3.1.1, which is documented here:
#  https://jinja.palletsprojects.com/en/3.1.x/

# standard libraries
import sys

# third party libraries
from absl import app, flags, logging

from tools.codegen.codegen_lib import Template

FLAGS = flags.FLAGS
flags.DEFINE_multi_string("load", [], "List of data files to load.")
flags.DEFINE_multi_string("override", [], "List of key=value pair data.")
flags.DEFINE_string("schema", None, "JSON schema to check against.")
flags.DEFINE_multi_string("output", None, "Output files to generate.")
flags.DEFINE_boolean("to_stdout", False, "Writes all output to stdout.")
flags.DEFINE_multi_string("incdir", [], "Paths to search for template files.")
flags.DEFINE_boolean("multigen_mode", False, "Generates a zip file containing a file for each data context index.")


def main(argv):
    context = Template(incdirs=FLAGS.incdir, schema=FLAGS.schema)
    for path in FLAGS.load:
        context.LoadDataFile(path)
    for override in FLAGS.override:
        context.Override(override)
    context.FixToplevelNames()
    for path in argv[1:]:
        t = Template(context)
        t.LoadTemplate(path)
        if FLAGS.multigen_mode:
            if len(FLAGS.output) != 1:
                logging.error("Exactly one output zip file must be specified in multigen mode.")
                sys.exit(1)
            t.MultigenRenderToOutput(FLAGS.output[0], to_stdout = FLAGS.to_stdout)
        elif FLAGS.output:
            for path in FLAGS.output:
                t.RenderToOutput(path, to_stdout = FLAGS.to_stdout)
        else:
            t.RenderToOutput(t.InferOutputFile(), to_stdout = FLAGS.to_stdout)


if __name__ == "__main__":
    app.run(main)
