#!/usr/bin/python

import logging
import os
import re
import subprocess
import sys

import buildpy.vx


def _setup_logger():
    logger = logging.getLogger()
    hdl = logging.StreamHandler(sys.stderr)
    hdl.setFormatter(logging.Formatter("%(levelname)s\t%(process)d\t%(asctime)s\t%(filename)s\t%(funcName)s\t%(lineno)d\t%(message)s"))
    logger.addHandler(hdl)
    logger.setLevel(logging.DEBUG)
    return logger


logger = _setup_logger()


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable

python = os.environ["PYTHON"]


dsl = buildpy.vx.DSL(use_hash=True)
file = dsl.file
phony = dsl.phony
loop = dsl.loop
sh = dsl.sh
rm = dsl.rm


all_files = set(
    subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        universal_newlines=True,
        stdout=subprocess.PIPE,
    ).stdout.rstrip("\0").split("\0")
)
py_files = set(path for path in all_files if path.endswith(".py"))
buildpy_files =set(path for path in all_files if path.startswith(os.path.join("buildpy", "v")))
vs = set(path.split(os.path.sep)[1] for path in buildpy_files)
test_files = set(path for path in buildpy_files if re.match(os.path.join("^buildpy", "v[0-9]+", "tests"), path))

buildpy_py_files = list(py_files.intersection(buildpy_files) - test_files)


phony("all", [], desc="The default target")


@phony("sdist", [], desc="Make a distribution file")
def _(j):
    sh(f"""
    git ls-files -z |
    xargs -0 -n1 echo include >| MANIFEST.in
    {python} setup.py sdist
    """)


phony("check", [], desc="Run tests")


@loop(vs)
def _(v):
    v_files = [path for path in all_files if path.startswith(os.path.join("buildpy", v))]
    v_test_files = [path for path in v_files if path.startswith(os.path.join("buildpy", v, "tests"))]
    v_py_files = list(set(v_files).intersection(set(buildpy_py_files)))

    @loop(path for path in v_test_files if path.endswith(".sh"))
    def _(test_sh):
        test_sh_done = test_sh + ".done"
        phony("check", [test_sh_done])

        @file([test_sh_done], [test_sh] + v_py_files, desc=f"Test {test_sh}")
        def _(j):
            sh(f"""
            {j.ds[0]}
            touch {j.ts[0]}
            """)

    @loop(path for path in v_test_files if path.endswith(".py"))
    def _(test_py):
        test_py_done = test_py + ".done"
        phony("check", [test_py_done])

        @file([test_py_done], [test_py] + v_py_files, desc=f"Test {test_py}", priority=-1)
        def _(j):
            sh(f"""
            {python} {j.ds[0]}
            touch {j.ts[0]}
            """)


if __name__ == '__main__':
    dsl.main(sys.argv)
