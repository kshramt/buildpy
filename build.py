#!/usr/bin/python

import os
import re
import subprocess
import sys

import buildpy


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


__dsl = buildpy.DSL()
file = __dsl.file
phony = __dsl.phony
sh = __dsl.sh
rm = __dsl.rm


all_files = set(
    subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        universal_newlines=True,
        stdout=subprocess.PIPE,
    ).stdout.split("\0")
)
py_files = set(path for path in all_files if path.endswith(".py"))
buildpy_files =set(path for path in all_files if path.startswith(os.path.join("buildpy", "v")))
vs = set(path.split(os.path.sep)[1] for path in buildpy_files)
test_files = set(path for path in buildpy_files if re.match(os.path.join("^buildpy", "v[0-9]+", "tests"), path))

buildpy_py_files = list(py_files.intersection(buildpy_files) - test_files)


def let():
    phony("all", [], desc="The default target")

    @phony("sdist", [], desc="Make distribution file")
    def _(j):
        sh(f"""
        git ls-files |
        while read line
        do
            echo include "$line"
        done >| MANIFEST.in
        {os.environ["PYTHON"]} setup.py sdist
        """)

    phony("check", [], desc="Run tests")
    for v in vs:
        v_files = [path for path in all_files if path.startswith(os.path.join("buildpy", v))]
        v_test_files = [path for path in v_files if path.startswith(os.path.join("buildpy", v, "tests"))]
        def let():
            for test_sh in [path for path in v_test_files if path.endswith(".sh")]:
                test_sh_done = test_sh + ".done"
                phony("check", [test_sh_done])

                @file([test_sh_done], [test_sh] + buildpy_py_files, desc=f"Test {test_sh}")
                def _(j):
                    sh(f"""
                    {j.ds[0]}
                    touch {j.ts[0]}
                    """)
        let()

        def let():
            for test_py in [path for path in v_test_files if path.endswith(".py")]:
                test_py_done = test_py + ".done"
                phony("check", [test_py_done])

                @file([test_py_done], [test_py] + buildpy_py_files, desc=f"Test {test_py}")
                def _(j):
                    sh(f"""
                    {os.environ["PYTHON"]} {j.ds[0]}
                    touch {j.ts[0]}
                    """)
        let()
let()


if __name__ == '__main__':
    __dsl.main(sys.argv)
