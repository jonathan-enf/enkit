"""fix_format: Tool to check and/or fix formatting of source files.

Note: log.debug can't be used because the "black" library abuses it. Use
log.vlog(1, ...) instead.
"""
# standard libraries
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# third party libraries
import black
import isort
import toml
from absl import app, flags
from absl import logging as log
from enkit.tools.mdfmt import mdfmt
from rules_python.python.runfiles import runfiles

# enfabrica libraries
from infra.lib import paths, stamp

RUNFILES_ENV = os.environ

# Checker objects
# Each object should implement:
#     filter(files) - returns a list subset of `files` that this formatter
#         should handle
#     check(files) - returns a string if any files should be reformatted, or no
#         string if all files are formatted. The string will be presented to the
#         user, and should contain the files to be formatted.
#     fix(files) - returns a string if any fix operations failed, or no string
#         if the fix was successful. The string will be presented to the user as
#         an error message if an error occurs.


class BzlFormatter(object):
    """Handles formatting for Starlark and BUILD files."""

    def __init__(self):
        r = runfiles.Create()
        self.buildifier = pathlib.Path(r.Rlocation("com_github_bazelbuild_buildtools/buildifier/buildifier_/buildifier"))

    def filter(self, files):
        return filter_files(
            files,
            [
                "^.*\\.bazel$",
                "^.*\\.bzl$",
                "^.*BUILD$",
                "^.*WORKSPACE$",
            ],
            [
                "^.*\\.json\\.bzl$",
            ],
        )

    def check(self, files):
        log.vlog(1, "BzlFormatter: checking %r", files)
        return output_if_fails(
            [
                self.buildifier,
                "-v",
                "--mode=check",
                "-add_tables",
                source_file_path("bazel/buildifier.tables.json"),
            ]
            + files,
        )

    def fix(self, files):
        log.vlog(1, "BzlFormatter: fixing %r", files)
        return output_if_fails(
            [
                self.buildifier,
                "-v",
                "-add_tables",
                source_file_path("bazel/buildifier.tables.json"),
            ]
            + files,
        )


class MdFormatter(object):
    """Handles formatting for Markdown files."""

    def filter(self, files):
        return filter_files(files, ["^.*\\.md$"])

    def check(self, files):
        log.vlog(1, "MdFormatter: checking %r", files)
        errors = []
        for f in files:
            ok = mdfmt.format_file(source_file_path(f), source_file_path(f), check=True)
            if not ok:
                errors.append(f"{f!r} needs reformatting.")
        return errors

    def fix(self, files):
        log.vlog(1, "MdFormatter: fixing %r", files)
        errors = []
        for f in files:
            ok = mdfmt.format_file(source_file_path(f), source_file_path(f), check=False)
            if not ok:
                errors.append(f"Error formatting {f!r}.")
        return errors


class CodeownersFormatter(object):
    """Format the CODEOWNERS file."""

    def filter(self, files):
        return filter_files(files, ["^CODEOWNERS$"])

    def _format_code(self, text: str) -> str:
        """Format the contents of a CODEOWNERS file.

        Right now, the only change made is enforcing that usernames
        are listed in alphabetical order for easier maintenance.
        """
        lines_out = []
        re_comment = re.compile(r"(\s*#.*)")
        for line in text.splitlines():
            parts = re_comment.split(line)
            if parts[0]:
                words = parts[0].split()
                label = words.pop(0)
                words.sort()
                words.insert(0, label)
                parts[0] = " ".join(words)
            line = "".join(parts)
            lines_out.append(line)
        return (True, "\n".join(lines_out) + "\n")

    def check(self, files):
        log.vlog(1, "CodeownersFormatter: checking %r", files)
        errors = []
        for f in files:
            with open(source_file_path(f), "r", encoding="utf-8") as fd:
                contents = fd.read()
            ok, dst_contents = self._format_code(contents)
            if ok:
                if contents != dst_contents:
                    errors.append(f"{f!r} needs reformatting.")
            else:
                errors.append(f"Error formatting {f!r}")
        return errors

    def fix(self, files):
        log.vlog(1, "CodeownersFormatter: fixing %r", files)
        errors = []
        for f in files:
            with open(source_file_path(f), "r", encoding="utf-8") as fd:
                contents = fd.read()
            ok, dst_contents = self._format_code(contents)
            if ok:
                with open(source_file_path(f), "w", encoding="utf-8") as fd:
                    fd.write(dst_contents)
            else:
                errors.append(f"Error formatting {f!r}")
        return errors


class PyFormatter(object):
    """Handles formatting for Python files."""

    def __init__(self):
        # black doesn't really have an API: https://github.com/psf/black/issues/779
        # we carry on as best we can.
        tomlfile = source_file_path("pyproject.toml")
        tomldata = toml.load(tomlfile)
        config = tomldata.get("tool", {}).get("black", {})
        config = {k.replace("--", "").replace("-", "_"): v for k, v in config.items()}
        self.mode = black.Mode()
        self.mode.line_length = config.get("line_length", 160)
        # TODO(jonathan): parse out the rest of the config file settings and
        # map them onto black.Mode elements, once black has a stable API for
        # doing so.
        self.mode.target_versions = {
            black.mode.TargetVersion.PY36,
            black.mode.TargetVersion.PY37,
            black.mode.TargetVersion.PY38,
            black.mode.TargetVersion.PY39,
        }
        # Configure isort
        isortcfg_path = str(source_file_path(".isort.cfg"))
        self.isortcfg = isort.settings.Config(settings_file=isortcfg_path)

    def filter(self, files):
        return filter_files(files, ["^.*\\.py$"])

    def _format_code(self, contents: str) -> (bool, str):
        try:
            contents = isort.code(contents, config=self.isortcfg)
        except Exception as e:  # pylint: disable=broad-except
            print(f"isort failed: {e!r}")
            return (False, contents)

        try:
            contents = black.format_file_contents(src_contents=contents, fast=True, mode=self.mode)
        except black.NothingChanged:
            pass
        except Exception as e:  # pylint: disable=broad-except
            print(f"black failed: {e!r}")
            return (False, contents)
        return (True, contents)

    def check(self, files):
        log.vlog(1, "PyFormatter: checking %r", files)
        errors = []
        for f in files:
            with open(source_file_path(f), "r", encoding="utf-8") as fd:
                contents = fd.read()
            ok, dst_contents = self._format_code(contents)
            if ok:
                if contents != dst_contents:
                    errors.append(f"{f!r} needs reformatting.")
            else:
                errors.append(f"Error formatting {f!r}")
        return errors

    def fix(self, files):
        log.vlog(1, "PyFormatter: fixing %r", files)
        errors = []
        for f in files:
            with open(source_file_path(f), "r", encoding="utf-8") as fd:
                contents = fd.read()
            ok, dst_contents = self._format_code(contents)
            if ok:
                with open(source_file_path(f), "w", encoding="utf-8") as fd:
                    fd.write(dst_contents)
            else:
                errors.append(f"Error formatting {f!r}")
        return errors


class CFormatter(object):
    """Handles formatting for C files."""

    def __init__(self):
        self.clang_format = "clang-format-10"

    def filter(self, files):
        c_files = filter_files(files, ["^.*\\.c$"])
        h_files = filter_files(files, ["^.*\\.h$"])
        h_files = [f for f in h_files if header_type(source_file_path(f)) == "c"]
        return sorted(c_files + h_files)

    def check(self, files):
        log.vlog(1, "CFormatter: checking %r", files)
        backup = try_backup_file(source_file_path(".clang-format"))
        shutil.copy(source_file_path("c.clang-format"), source_file_path(".clang-format"))
        check_result = output_if_fails([self.clang_format, "--dry-run", "-Werror", "--verbose", "--style=file", "-i"] + files)
        os.remove(source_file_path(".clang-format"))
        try_restore_file(source_file_path(".clang-format"), backup)
        return check_result

    def fix(self, files):
        log.vlog(1, "CFormatter: fixing %r", files)
        backup = try_backup_file(source_file_path(".clang-format"))
        shutil.copy(source_file_path("c.clang-format"), source_file_path(".clang-format"))
        fix_result = output_if_fails([self.clang_format, "-Werror", "--verbose", "--style=file", "-i"] + files)
        os.remove(source_file_path(".clang-format"))
        try_restore_file(source_file_path(".clang-format"), backup)
        return fix_result


class CppFormatter(object):
    """Handles formatting for C++ files."""

    def __init__(self):
        self.clang_format = "clang-format-10"

    def filter(self, files):
        cpp_files = filter_files(files, ["^.*\\.cc$", "^.*\\.cpp$"])
        hpp_files = filter_files(files, ["^.*\\.hh$", "^.*\\.hpp$"])
        h_files = filter_files(files, ["^.*\\.h$"])
        h_files = [f for f in h_files if header_type(source_file_path(f)) == "c++"]
        return sorted(cpp_files + hpp_files + h_files)

    def check(self, files):
        log.vlog(1, "CppFormatter: checking %r", files)
        backup = try_backup_file(source_file_path(".clang-format"))
        shutil.copy(source_file_path("cc.clang-format"), source_file_path(".clang-format"))
        check_result = output_if_fails([self.clang_format, "--dry-run", "-Werror", "--verbose", "--style=file", "-i"] + files)
        os.remove(source_file_path(".clang-format"))
        try_restore_file(source_file_path(".clang-format"), backup)
        return check_result

    def fix(self, files):
        log.vlog(1, "CppFormatter: fixing %r", files)
        backup = try_backup_file(source_file_path(".clang-format"))
        shutil.copy(source_file_path("cc.clang-format"), source_file_path(".clang-format"))
        fix_result = output_if_fails([self.clang_format, "-Werror", "--verbose", "--style=file", "-i"] + files)
        os.remove(source_file_path(".clang-format"))
        try_restore_file(source_file_path(".clang-format"), backup)
        return fix_result


class VerilogFormatter(object):
    """Handles formatting for Verilog files."""

    def __init__(self):
        r = runfiles.Create()
        self.verible = pathlib.Path(r.Rlocation("verible/verilog/tools/formatter/verible-verilog-format"))

    def filter(self, files):
        return filter_files(
            files,
            [
                "^.*\\.v$",
                "^.*\\.sv$",
                "^.*\\.svh$",
            ],
        )

    def check(self, files):
        log.vlog(1, "VerilogFormatter: checking %r", files)
        needs_format = [str(f) for f in files if hash_stdout([self.verible, "--inplace=false", source_file_path(f)]) != hash_file_contents(source_file_path(f))]
        if needs_format:
            msg = "\n\t".join(needs_format)
            return f"The following files need formatting:\n\t{msg}\n"
        return

    def fix(self, files):
        log.vlog(1, "VerilogFormatter: fixing %r", files)
        log.info("Fixing Verilog files: %s", [str(f) for f in files])
        errs = [output_if_fails([self.verible, "--verbose", "--inplace", source_file_path(f)]) for f in files]
        errs = [e for e in errs if e]  # Filter out empty strings
        if errs:
            msg = "\n\t".join(errs)
            return f"The following errors were encountered:\n\t{msg}\n"
        return ""


class RustFormatter(object):
    """Handles formatting for Rust files."""

    def __init__(self):
        r = runfiles.Create()
        self.rustfmt = pathlib.Path(r.Rlocation("enfabrica/tools/fix_format/rustfmt"))
        self.configfile = source_file_path(".rustfmt.toml")

    def filter(self, files):
        return filter_files(files, ["^.*\\.rs$"])

    def check(self, files):
        log.vlog(1, "RustFormatter: checking %r", files)
        return output_if_fails([self.rustfmt, "--config-path", self.configfile, "--check"] + files)

    def fix(self, files):
        log.vlog(1, "RustFormatter: fixing %r", files)
        return output_if_fails([self.rustfmt, "--config-path", self.configfile] + files)


def subprocess_run(cmd, **kwargs):
    """Replacement for subprocess.run() that adds some debug logging."""
    log.vlog(1, "Running command: %s", cmd)
    return subprocess.run(cmd, cwd=paths.workspace_root(), env=RUNFILES_ENV, **kwargs)  # pylint: disable=subprocess-run-check


def exits_success(cmd):
    """subprocess.run that returns True if the command succeeds and False if it fails."""
    ret = subprocess_run(cmd, encoding="utf-8")
    return ret.returncode == 0


def output_if_fails(cmd):
    """subprocess.run that returns stdout+stderr if the command fails, and empty string otherwise."""
    log.vlog(1, "Running command: %r", cmd)
    ret = subprocess_run(cmd, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if ret.returncode:
        return ret.stdout
    return ""


def stdout_lines(cmd):
    """subprocess.run that asserts that the command succeeds, and returns stdout split into lines."""
    ret = subprocess_run(cmd, check=True, encoding="utf-8", capture_output=True)
    return ret.stdout.splitlines()


def hash_stdout(cmd):
    """subprocess.run that asserts that the command succeeds, and returns the SHA256 of stdout."""
    ret = subprocess_run(cmd, check=True, capture_output=True)
    checksum = hashlib.sha256(ret.stdout).hexdigest()
    log.vlog(1, "sha256(stdout) = %s", checksum)
    return checksum


def hash_file_contents(f):
    """Returns the SHA256 digest of the file."""
    checksum = stdout_lines(["sha256sum", f])[0].strip().split()[0]
    log.vlog(1, "sha256(%s) = %s", f, checksum)
    return checksum


def changed_files():
    """Returns the set of files that the formatter should check/fix."""
    return deduplicate(changed_files_from_master(), untracked_files())


def changed_files_from_master():
    """Returns the list of added/changed/modified files between the current commit and master."""
    return stdout_lines(["git", "diff", "--diff-filter=ACM", "--name-only", "master...HEAD"])


def file_contains_string(f, s):
    """Returns True if the file named by f contains the string s."""
    return exits_success(["grep", "--silent", "-F", s, f])


def header_type(f):
    f = pathlib.Path(f)
    if f.with_suffix(".c").exists():
        return "c"
    elif f.with_suffix(".cc").exists():
        return "c++"
    elif f.with_suffix(".cpp").exists():
        return "c++"
    elif file_contains_string(f, "FORMAT=C99"):
        return "c"
    log.warning("Could not determine if %r was C++ or C99", str(f))
    return "unknown"


def try_backup_file(f):
    if not f.exists():
        log.vlog(1, "%s not found; skipping backup", f)
        return None
    (_, name) = tempfile.mkstemp(".clang-format", "fix_format_backup_", text=True)
    log.info("Backing up %s as %s", f, name)
    shutil.copy(f, name)
    return name


def try_restore_file(dest, backup):
    if not backup:
        return
    log.info("Restoring backup %s to %s", backup, dest)
    shutil.move(backup, dest)


def filter_files(files, include_regexps=None, exclude_regexps=None):
    # Only include files if they match at least one of include_regexps
    if include_regexps:
        files = [f for f in files if any(re.match(regexp, str(f)) for regexp in include_regexps)]
    # Only include files if they don't match any of exclude_regexps
    if exclude_regexps:
        files = [f for f in files if all(not re.match(regexp, str(f)) for regexp in exclude_regexps)]
    # Skip files that contain a special string indicating that they should not
    # be reformatted.  This string is escaped, below, so that fix_format will
    # format itself.
    fix_format_skip = "_".join(("FIX", "FORMAT", "SKIP"))
    files = [f for f in files if not file_contains_string(f, fix_format_skip)]
    return files


def untracked_files():
    status = stdout_lines(["git", "status", "--porcelain", "-uall"])
    files = [f.strip().split()[1] for f in status]
    return [f for f in files if source_file_path(f).exists()]


def source_file_path(f):
    return paths.workspace_relative(f)


def relative_from_root(f):
    f = paths.invocation_dir_relative(f)
    return f.relative_to(paths.workspace_root())


def deduplicate(*args):
    s = set()
    s.update(*args)
    return sorted(s)


def init_repo_root():
    global RUNFILES_ENV
    RUNFILES_ENV = dict(os.environ).update(runfiles.Create().EnvVars())


def run_check(formatter, files):
    files = formatter.filter(files)
    if not files:
        return ""
    log.info("Running format checker %s on files: %s", type(formatter).__name__, [str(f) for f in files])
    return formatter.check(files)


def run_fix(formatter, files):
    files = formatter.filter(files)
    if not files:
        return ""
    log.info("Running format fixer %s on files: %s", type(formatter).__name__, [str(f) for f in files])
    return formatter.fix(files)


def format_output(output_dict):
    ret = 0
    for name, output in output_dict.items():
        if output:
            ret = 1
            print(f"{name} needs formatting\nOutput:")
            print("-" * 80)
            print(output)
            print("-" * 80)
    if ret:
        print("\n" + "#" * 80)
        print("# Please fix formatting issues by running:                                     #")
        print("#                                                                              #")
        print("#     bazel run //tools/fix_format                                             #")
        print("#" * 80)
    return ret


def filter_files_exist(files):
    good_files = []
    for f in files:
        p = str(source_file_path(f))
        if not os.path.isfile(p):
            log.error("%r is not a file.", p)
        else:
            good_files.append(f)
    return good_files


def without(l, *args):
    """Returns list l with elements from args removed, if present"""
    return list(set(l) - set(args))


################################################################################
# Flags + main()
################################################################################

ALL_CHECKERS = {
    "bazel": BzlFormatter,
    "codeowners": CodeownersFormatter,
    "markdown": MdFormatter,
    "python": PyFormatter,
    "c": CFormatter,
    "cpp": CppFormatter,
    "verilog": VerilogFormatter,
    "rust": RustFormatter,
}

FLAGS = flags.FLAGS

flags.DEFINE_enum(
    "mode", "fix", ["fix", "check"], "Mode in which to run. fix=reformat files in-place; check=fail if files need reformatting, but make no changes"
)
flags.DEFINE_multi_enum(
    "lang",
    without(
        ALL_CHECKERS.keys(),
        # Exclude Verilog by default since it is not clear if teams like the
        # Verilog formatter.
        "verilog",
    ),
    ALL_CHECKERS.keys(),
    "Languages to fix/check",
)
flags.DEFINE_bool("version", False, "Report fix_format version.")
flags.DEFINE_bool("debug", False, "Enable debug log messages.")


def main(argv):
    if FLAGS.version:
        print("fix_format.py version report:")
        buildinfo = stamp.get_buildstamp_values()
        for k, v in buildinfo.items():
            print(f"  {k}: {v}")
        if not buildinfo:
            # Always supply --stamp when compiling/deploying fix_format.
            print("  Version information was not compiled in. :_(")
    init_repo_root()
    if argv[1:]:
        files = [relative_from_root(f) for f in argv[1:]]
    else:
        files = changed_files()
    formatters = {lang: ALL_CHECKERS[lang]() for lang in FLAGS.lang}
    files = filter_files_exist(files)
    if not files:
        log.error("No valid files to process.")
        sys.exit(1)
    if FLAGS.mode == "check":
        log.info("Files to check: %r", [str(f) for f in files])
        checker_out = {name: run_check(formatter, files) for (name, formatter) in formatters.items()}
        sys.exit(format_output(checker_out))
    elif FLAGS.mode == "fix":
        log.info("Files to process: %r", [str(f) for f in files])
        fix_out = {name: run_fix(formatter, files) for (name, formatter) in formatters.items()}
        sys.exit(format_output(fix_out))
    else:
        log.error("Unhandled --mode: %s", FLAGS.mode)
        sys.exit(2)


if __name__ == "__main__":
    app.run(main)
