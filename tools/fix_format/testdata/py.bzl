"""Defines rules to for building, testing, and linting Python files.

Prefer pytest_test over the default py_.* rule provided by bazel.

Supports exactly the same parameters and flags, refer to the bazel
documentation here: https://docs.bazel.build/versions/master/be/python.html#py_test
for details.

The main difference is that they pass default options and parameters desired
for the builds at enfabrica.

Over time, the rules will be updated to use an hermetic toolchain, and better
match our needs. Right now, they are mostly a placeholder setting basic language
constraints.

Pytest wrapper methodology credit to:
    https://dev.to/davidb31/experimentations-on-bazel-python-3-linter-pytest-49oh
    https://dev.to/davidb31/experimentations-on-bazel-python-2-linter-52
"""

load("@rules_python//python:defs.bzl", "py_test")
load("@internal_python_pip//:requirements.bzl", "requirement")

pylint_dependencies = [
    requirement("pylint"),
]

isort_dependencies = [
    requirement("isort"),
]

black_dependencies = [
    requirement("black"),
]

pytest_dependencies = [
    requirement("pytest"),
    requirement("pytest-black"),
    requirement("pytest-custom_exit_code"),
    requirement("pytest-order"),
    requirement("pytest-profiling"),
]

def en_py_library(name, srcs, deps = [], lint = True, isort = True, format = True, *args, **kwargs):
    """Wrapper for py_library that includes various checks for srcs:

    * lint checks, via pylint
    * import ordering checks, via isort
    * formatting checks, via black

    Args:
      name: (str) A unique name for this target; required
      srcs: (list) List of labels; required
      deps: (list) List of labels; optional
      lint: (bool) If true, generate a test target that lints srcs
      isort: (bool) If true, generate a test target that checks import ordering
      format: (bool) If true, generate a test target that checks formatting of srcs
      *args: (list) Extra attributes to pass to underlying py_library
      **kwargs: (Dict) Extra attributes to pass to py_library
    """
    if lint:
        _lint_test(
            name = name + "-lint_test",
            srcs = srcs,
            deps = deps,
        )

    if isort:
        _isort_test(
            name = name + "-isort_test",
            srcs = srcs,
            deps = deps,
        )

    if format:
        _format_test(
            name = name + "-format_test",
            srcs = srcs,
        )

    native.py_library(
        name = name,
        srcs = srcs,
        deps = deps,
        *args,
        **kwargs
    )

def en_py_binary(name, srcs, deps = [], lint = True, isort = True, format = True, *args, **kwargs):
    """Wrapper for py_binary that includes various checks for srcs:

    * lint checks, via pylint
    * import ordering checks, via isort
    * formatting checks, via black

    Args:
      name: (str) A unique name for this target; required
      srcs: (list) List of labels; required
      deps: (list) List of labels; optional
      lint: (bool) If true, generate a test target that lints srcs
      isort: (bool) If true, generate a test target that checks import ordering
      format: (bool) If true, generate a test target that checks formatting of srcs
      *args: (list) Extra attributes to pass to underlying py_binary
      **kwargs: (Dict) Extra attributes to pass to py_binary
    """
    if lint:
        _lint_test(
            name = name + "-lint_test",
            srcs = srcs,
            deps = deps,
        )

    if isort:
        _isort_test(
            name = name + "-isort_test",
            srcs = srcs,
            deps = deps,
        )

    if format:
        _format_test(
            name = name + "-format_test",
            srcs = srcs,
        )

    native.py_binary(
        name = name,
        srcs = srcs,
        deps = deps,
        *args,
        **kwargs
    )

def en_py_test(name, srcs, deps = [], args = [], data = [], lint = True, isort = True, format = True, debug = False, profile = False, **kwargs):
    """Wrapper for py_test that includes various checks for srcs:

    * lint checks, via pylint
    * import ordering checks, via isort
    * formatting checks, via black

    Args:
      name: (str) A unique name for this target; required
      srcs: (list) List of labels; required
      deps: (list) List of labels; optional
      args: (list) List of arguments to pass to pytest
      data: (list) List of labels; optional
      lint: (bool) If true, generate a test target that lints srcs
      isort: (bool) If true, generate a test target that checks import ordering
      format: (bool) If true, generate a test target that checks formatting of srcs
      debug: (bool) If true, returns native.py_binary() to allow interactive pdb
      **kwargs: (Dict) Extra attributes to pass to py_test
    """
    if lint:
        _lint_test(
            name = name + "-lint_test",
            srcs = srcs,
            deps = deps,
        )

    if isort:
        _isort_test(
            name = name + "-isort_test",
            srcs = srcs,
            deps = deps,
        )

    if format:
        _format_test(
            name = name + "-format_test",
            srcs = srcs,
        )

    # Some consumers of en_py_test will list "pytest" in deps but py_test() will barf if there are duplicate
    # entries in deps
    deps = [x for x in deps if x not in pytest_dependencies]

    if debug:
        # When debug=True, return py_binary which leaves stdin open
        # allowing for import pdb; pdb.set_trace() debugging
        # when bazel running the target
        return native.py_binary(
            name = name,
            srcs = srcs,
            deps = deps + pytest_dependencies,
            *args,
            **kwargs
        )

    if profile:
        args = [
            "--profile",
            "--profile-svg",
        ] + args

    return native.py_test(
        name = name,
        srcs = [
            "//bazel/python:wrapper_pytest.py",
        ] + srcs,
        main = "//bazel/python:wrapper_pytest.py",
        args = [
            # Generate a standard junit XML-formatted test.xml file.
            # This is required for test case result processing by the BES Endpoint.
            "--junitxml=$$XML_OUTPUT_FILE",
        ] + args + ["$(location :%s)" % x for x in srcs],
        python_version = "PY3",
        srcs_version = "PY3",
        deps = deps + pytest_dependencies,
        data = [
            "//:.pylintrc",
            "//:pyproject.toml",
            "//:pytest.ini",
        ] + data,
        **kwargs
    )

def _isort_test(name, srcs, deps = [], args = [], data = [], **kwargs):
    """Invokes isort over specified list of fails, failing if any of the files need re-formatting.

    Args:
      name: (str) A unique name for this target; required
      srcs: (list) List of labels; required
      deps: (list) List of labels; optional
      args: (list) Any pass-thru arguments to supply to pylint; optional
      data: (list) Any data dependencies to be included in the pylint invocation; optional
      **kwargs: (Dict): Extra attributes to pass to py_test
    """
    py_test(
        name = name,
        srcs = [
            "//bazel/python:wrapper_isort.py",
        ] + srcs,
        main = "//bazel/python:wrapper_isort.py",
        args = ["--check"] + args + ["$(location :%s)" % x for x in srcs],
        python_version = "PY3",
        srcs_version = "PY3",
        deps = deps + isort_dependencies,
        data = [
            "//:.isort.cfg",
        ] + data,
        **kwargs
    )

def _lint_test(name, srcs, deps = [], args = [], data = [], **kwargs):
    """Invokes standard Python pylint providing some common arguments.

    Args:
      name: (str) A unique name for this target; required
      srcs: (list) List of labels; required
      deps: (list) List of labels; optional
      args: (list) Any pass-thru arguments to supply to pylint; optional
      data: (list) Any data dependencies to be included in the pylint invocation; optional
      **kwargs: (Dict) Extra attributes to pass to py_test
    """
    native.py_test(
        name = name,
        srcs = [
            "//bazel/python:wrapper_pylint.py",
        ] + srcs,
        main = "//bazel/python:wrapper_pylint.py",
        args = args + ["$(location :%s)" % x for x in srcs],
        python_version = "PY3",
        srcs_version = "PY3",
        deps = deps + pylint_dependencies,
        data = [
            "//:.pylintrc",
        ] + data,
        **kwargs
    )

def _format_test(name, srcs, **kwargs):
    native.py_test(
        name = name,
        srcs = [
            "//bazel/python:wrapper_black.py",
        ] + srcs,
        main = "//bazel/python:wrapper_black.py",
        args = ["--check"] + ["$(location :%s)" % src for src in srcs],
        python_version = "PY3",
        srcs_version = "PY3",
        deps = black_dependencies,
        data = [
            "//:pyproject.toml",
        ],
        **kwargs
    )

# TODO(INFRA-452): Remove once this is no longer called directly anywhere
def en_py_test_isort(name, srcs, deps = [], args = [], data = [], **kwargs):
    _isort_test(
        name = name,
        srcs = srcs,
        deps = deps,
        args = args,
        data = data,
        deprecation = "Wrap all Python files with en_py_test, en_py_library, en_py_binary rules (which all generate isort checks) instead of using en_py_test_isort directly",
        **kwargs
    )

# TODO(INFRA-452): Remove once this is no longer called directly anywhere
def en_py_lint(name, srcs, deps = [], args = [], data = [], **kwargs):
    _lint_test(
        name = name,
        srcs = srcs,
        deps = deps,
        args = args,
        data = data,
        deprecation = "Wrap all Python files with en_py_test, en_py_library, en_py_binary rules (which all generate lint checks) instead of using en_py_lint directly",
        **kwargs
    )

def _py_files_impl(ctx):
    inputs = [] + ctx.files.srcs
    outputs = []
    arguments = [] + ctx.attr.flags

    if len(ctx.files.srcs) != len(ctx.attr.inputs.keys()):
        fail("srcs and inputs must be the same length")

    # Ordering is preserved because bazel uses an OrderedDict()
    for i in range(len(ctx.attr.srcs)):
        arguments += [ctx.attr.inputs.keys()[i]] + [f.path for f in ctx.attr.srcs[i].files.to_list()]

    for key, val in ctx.attr.outputs.items():
        out = ctx.actions.declare_file(val)
        outputs += [out]
        arguments += [key, out.path]

    for key, val in ctx.attr.args.items():
        arguments += [key, val]

    ctx.actions.run(
        inputs = inputs,
        executable = ctx.executable.script,
        arguments = arguments,
        outputs = outputs,
    )
    return [DefaultInfo(files = depset(outputs))]

_py_files = rule(
    doc = """Runs a generic python script that generates files.
The python script must follow this format:
script.py [--key val --key val ...] --outputs file file ...
where key-value argument pairs are optional while
file output names are required. If the python script requires input files,
then add these inputs to srcs and args.

Refer to the absl python package for python script args parsing best-practices
https://abseil.io/docs/python/quickstart
""",
    implementation = _py_files_impl,
    attrs = {
        "debug": attr.bool(
            doc = "Set to true to turn on debugging of the commands run",
            mandatory = False,
        ),
        "script": attr.label(
            doc = "Executable python script that generates files",
            allow_files = True,
            executable = True,
            cfg = "exec",
            mandatory = True,
        ),
        "srcs": attr.label_list(
            doc = "List of source files consumed as input by the python script",
            allow_files = True,
            default = [],
        ),
        "inputs": attr.string_dict(
            doc = "Input --key and value pairs for the python script where values are bazel file targets",
            mandatory = False,
        ),
        "outputs": attr.string_dict(
            doc = "Output --key and value pairs generated by the python script where values are files",
            mandatory = True,
        ),
        "output_list": attr.output_list(
            doc = "A list of all outputs specified in the outputs map.",
            mandatory = True,
        ),
        "args": attr.string_dict(
            doc = "--key and value pairs where values are strings, integers, or floats used by the python script",
            mandatory = False,
        ),
        "flags": attr.string_list(
            doc = "List of --flag used by the python script",
            mandatory = False,
        ),
    },
)

def py_files(*args, **kwargs):
    kwargs["srcs"] = []

    # This seems to be necessary to explicitly declare all outputs in a way
    # that bazel's dependency graph can see them:
    kwargs["output_list"] = [] + kwargs["outputs"].values()
    kwargs["srcs"] = [Label(val) for val in kwargs.get("inputs", dict()).values()]
    return _py_files(*args, **kwargs)
