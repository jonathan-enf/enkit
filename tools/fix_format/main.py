"""fix_format: Tool to check and/or fix formatting of source files."""
# third party libraries
from absl import app

from tools.fix_format import fix_format

if __name__ == "__main__":
    app.run(fix_format.main)
