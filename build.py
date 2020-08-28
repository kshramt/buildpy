#!/usr/bin/python3

from argparse import Namespace as nas
import logging
import os
import re
from shlex import quote as esc
import subprocess
import sys

import buildpy.vx


def _setup_logger(level):
    logger = logging.getLogger()
    hdl = logging.StreamHandler(sys.stderr)
    hdl.setFormatter(
        logging.Formatter(
            "%(levelname)s %(process)d %(thread)d %(asctime)s %(filename)s %(lineno)d %(funcName)s %(message)s",
            "%y%m%d%H%M%S",
        )
    )
    logger.addHandler(hdl)
    hdl.setLevel(getattr(logging, level))
    logger.setLevel(getattr(logging, level))
    return logger


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable
os.environ["PYTHONPATH"] = os.getcwd() + (
    (":" + os.environ["PYTHONPATH"]) if "PYTHONPATH" in os.environ else ""
)

python = os.environ["PYTHON"]


dsl = buildpy.vx.DSL(sys.argv)
logger = _setup_logger(dsl.args.log)
logger.info(dsl.args.id)
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
    )
    .stdout.strip("\0")
    .split("\0")
)
py_files = set(path for path in all_files if path.endswith(".py"))
buildpy_files = set(
    path for path in all_files if path.startswith(os.path.join("buildpy", "v"))
)
vs = set(path.split(os.path.sep)[1] for path in buildpy_files)
test_files = set(
    path
    for path in buildpy_files
    if re.match(os.path.join("^buildpy", "v([0-9]+|x)", "tests"), path)
)

buildpy_py_files = list(py_files.intersection(buildpy_files) - test_files)


phony("all", [], desc="The default target")


@phony("sdist", [], desc="Make a distribution file")
def _(j):
    sh(
        f"""
rm -fr buildpy.egg-info
{python} setup.py sdist
        """
    )


check_jobs = []


@loop(vs)
def _(v):
    v_files = [
        path for path in all_files if path.startswith(os.path.join("buildpy", v))
    ]
    v_test_files = [
        path for path in v_files if path.startswith(os.path.join("buildpy", v, "tests"))
    ]
    v_py_files = list(set(v_files).intersection(set(buildpy_py_files)))

    check_jobs.append(f"check-{v}")
    check_v_jobs = []

    @loop(path for path in v_test_files if path.endswith(".sh"))
    def _(test_sh):
        test_sh_done = test_sh + ".done"

        @file(["done"], [test_sh] + v_py_files, desc=f"Test {test_sh}", auto=True)
        @dsl.with_symlink(test_sh_done)
        def job(j):
            sh(
                f"""
{j.ds[0]}
mkdir -p "$(dirname "{j.ts[0]}")"
touch {j.ts[0]}
                """
            )

        check_v_jobs.extend(job.ts_unique)

    @loop(path for path in v_test_files if path.endswith(".py"))
    def _(test_py):
        @file(
            "done",
            nas(exe=test_py, deps=v_py_files),
            desc=f"Test {test_py}",
            priority=-1,
            auto=True,
        )
        @dsl.with_symlink(test_py + ".done")
        def job(j):
            sh(
                f"""
{python} {esc(j.ds.exe)}
mkdir -p "$(dirname {esc(j.ts)})"
touch {esc(j.ts)}
                """
            )

        check_v_jobs.extend(job.ts_unique)

    phony(f"check-{v}", check_v_jobs)


phony("check", check_jobs, desc="Run tests")


if __name__ == "__main__":
    dsl.run()
    # print(dsl.dependencies_dot())
