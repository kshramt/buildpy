#!/usr/bin/python3

import logging
import os
import re
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
logger.info(dsl.id_dsl)
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
        check_v_jobs.append(test_sh_done)

        @file([test_sh_done], [test_sh] + v_py_files, desc=f"Test {test_sh}")
        def _(j):
            sh(
                f"""
                {j.ds[0]}
                touch {j.ts[0]}
                """
            )

    @loop(path for path in v_test_files if path.endswith(".py"))
    def _(test_py):
        test_py_done = test_py + ".done"
        check_v_jobs.append(test_py_done)

        @file(
            [test_py_done], [test_py] + v_py_files, desc=f"Test {test_py}", priority=-1
        )
        def _(j):
            sh(
                f"""
                {python} {j.ds[0]}
                touch {j.ts[0]}
                """
            )

    phony(f"check-{v}", check_v_jobs)


phony("check", check_jobs, desc="Run tests")


if __name__ == "__main__":
    dsl.run()
    # print(dsl.dependencies_dot())
