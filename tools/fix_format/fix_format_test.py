"""Test tools.fix_format.fix_format."""

# standard libraries
import os

# third party libraries
from absl.testing import absltest
from rules_python.python.runfiles import runfiles

# enfabrica libraries
from tools.fix_format import fix_format


class TestFixFormat(absltest.TestCase):
    """Test tools.fix_format.fix_format."""

    def setUp(self):
        os.environ["BUILD_WORKSPACE_DIRECTORY"] = os.getenv("TEST_SRCDIR") + "/enfabrica"
        self.runfiles = runfiles.Create()

    def test_rustfmt(self):
        f = "./tools/fix_format/testdata/lib.rs"
        output = fix_format.run_check(formatter=fix_format.RustFormatter(), files=[f])
        self.assertFalse(output)

    # re-enable this once we figure out how to mock flags properly:
    # @flagsaver.flagsaver
    # def test_builds_and_runs(self):
    #    files = "README.md base.cc base.h foo.py lib.rs py.bzl sub0.sv".split()
    #    resolved = [self.runfiles.Rlocation("tools/fixformat/testdata/%s" % x) for x in files]
    #
    #
    #    with self.assertRaises(SystemExit):
    #        args = ["fix_format", "--v=1", "--check", *resolved]
    #        flags.flags(args)
    #        fix_format.main(("fix_format", *resolved))


if __name__ == "__main__":
    absltest.main()
