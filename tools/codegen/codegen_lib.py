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
import json
import os
import re
import sys
import zipfile

# third party libraries
import jinja2
import jinja2.ext
import jsonschema
import yaml
from absl import logging

from tools.codegen import data_loader


class RaisedError(jinja2.TemplateError):
    def __init__(self, message=None):
        super().__init__(message)


class RaiseExtension(jinja2.ext.Extension):
    tags = {"raise"}

    def parse(self, parser):
        ln = next(parser.stream).lineno
        message = parser.parse_expression()
        return jinja2.nodes.CallBlock(self.call_method("_raise", [message], lineno=ln), [], [], [], lineno=ln)

    def _raise(self, msg, caller):
        raise RaisedError(msg)


def bitwise_and_function(a, b):
    return a & b


def bitwise_or_function(a, b):
    return a | b


def bitwise_xor_function(a, b):
    return a ^ b


def bitwise_not_function(a):
    return ~a


def re_split_function(regex, text):
    return re.split(regex, text)


def _merge(a, b, path=None):
    """Merges structure b into structure a."""
    if path is None:
        path = []
    if isinstance(b, (str, int, float)):
        a = b
    elif isinstance(a, dict) and isinstance(b, dict):
        for key in b:
            if key in a:
                a[key] = _merge(a[key], b[key], path + [key])
            else:
                a[key] = b[key]
    elif isinstance(a, list) and isinstance(b, list):
        a += b
    else:
        raise ("Could not merge type %r with type %r", type(a), type(b))
    return a


class Template(data_loader.DataLoader):
    def __init__(self, other=None, incdirs=[], schema=None):
        super(Template, self).__init__()
        self.schema = schema
        search_paths = ["."] + incdirs
        self.env = jinja2.Environment(
            extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols", "jinja2.ext.debug", RaiseExtension],
            loader=jinja2.FileSystemLoader(search_paths),
            keep_trailing_newline=True,
            autoescape=False,
        )
        self.env.globals.update(
            {
                "bitwise_and": bitwise_and_function,
                "bitwise_or": bitwise_or_function,
                "bitwise_xor": bitwise_xor_function,
                "bitwise_not": bitwise_not_function,
                "re_split": re_split_function,
            }
        )
        self.context = {"_DATA": [], "_TEMPLATE": ""}
        self.template = None
        self.template_path = None
        if other:
            self.context = other.context
            self.template = other.template
            self.template_path = other.template_path

    def GetContextKeys(self):
        return self.context.keys()

    def GetSubcontext(self, key):
        t = Template(self)
        t.context = {}
        if "_default" in self.context:
            t.context = _merge(t.context, self.context["_default"])
        t.context = _merge(t.context, self.context[key])
        return t

    def Override(self, override: str):
        k, v = override.split("=", 2)
        self.context = _merge(self.context, {k: v })

    def LoadDataFile(self, path: str):
        # TODO(jonathan): support protobuffer?
        # TODO(jonathan): support toml?
        ext = os.path.splitext(path)[-1]
        if ext == ".json":
            self.LoadJsonFile(path)
        elif ext == ".yaml" or ext == ".pkgdef":
            self.LoadYamlFile(path)
        else:
            logging.error("Unsupported data file extension %r: %r", ext, path)

    def LoadJsonFile(self, path: str):
        with open(path, "r") as fd:
            d = json.load(fd)
            self.context = _merge(self.context, d)
            self.context["_DATA"] += [path]
            logging.vlog(1, "Loaded data from %r", path)
            logging.vlog(2, "%r", d)

    def LoadYamlFile(self, path):
        with open(path, "r") as fd:
            d = yaml.safe_load(fd)
            self.context = _merge(self.context, d)
            self.context["_DATA"] += [path]
            logging.vlog(1, "Loaded data from %r", path)
            logging.vlog(2, "%r", d)

    def MergeData(self, data):
        self.context = _merge(self.context, data)

    def FixToplevelNames(self):
        """Normalize the toplevel keys in the data context.

        Jinja2 requires that all top-level keys in the data context have
        simple Python-compatible names.  This routine searches for keys
        with names like "$defs" and turns them into "_DOLLAR_defs".

        We also create a reference to the top-level context and name
        that context "_TOP".
        """
        keys = list(self.context.keys())  # because we modify keys.
        for key in keys:
            if "$" in key:
                alternate = key.replace("$", "_DOLLAR_")
                self.context[alternate] = self.context[key]
                del self.context[key]
        self.context["_TOP"] = self.context

    def LoadTemplate(self, path):
        logging.vlog(1, "Loading template %r", path)
        self.template_path = path
        self.template = self.env.get_template(path)

    def CheckSchema(self):
        # Raises jsonschema.exceptions.{ValidationError,SchemaError}
        if not self.schema:
            return
        with open(self.schema, "r") as fd:
            schema = None
            if self.schema.endswith(".yaml"):
                schema = yaml.safe_load(fd)
            else:
                schema = json.load(fd)
            logging.vlog(1, "Loaded schema %r", self.schema)
            jsonschema.validate(instance=self.context, schema=schema)

    def Render(self):
        self.CheckSchema()  # raises exception on error.
        text = self.template.render(self.context)
        text = re.sub(r"(?m)[ \t]+$", "", text)  # remove trailing whitespace
        return text

    def InferOutputFile(self):
        output_file = os.path.basename(self.template_path)
        for extension in (".jinja", ".jinja2", ".template", ".tmpl"):
            if output_file.endswith(extension):
                output_file = output_file[: -len(extension)]
        if output_file == os.path.basename(self.template_path):
            output_file += ".out"
        return output_file

    def MultigenRenderToOutput(self, output_file, to_stdout=False):
        with zipfile.ZipFile(output_file, mode="a") as zf:
            for k in self.GetContextKeys():
                if k.startswith("_"):
                    continue
                subt = t.GetSubcontext(k)
                subt.context["_OUTPUT_PATH"] = k
                subt.context["_OUTPUT_FILE"] = k
                output = subt.Render()
                zf.writestr(k, output)
                if to_stdout:
                    sys.stdout.write(output)
                    logging.info("Wrote %d bytes to stdout", len(output))
            zf.close()

    def RenderToOutput(self, output_file=None, to_stdout=False):
        if output_file:
            self.context["_OUTPUT_PATH"] = output_file
            self.context["_OUTPUT_FILE"] = os.path.basename(output_file)
        output = self.Render()
        if to_stdout:
            sys.stdout.write(output)
            logging.info("Wrote %d bytes to stdout", len(output))
        if output_file:
            with open(output_file, "w") as fd:
                fd.write(output)
                fd.close()
            logging.vlog(2, "Wrote %d bytes to %r", len(output), output_file)


