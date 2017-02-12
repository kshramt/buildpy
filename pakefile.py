#!/usr/bin/python

import os
import subprocess
import sys

import pake


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


__dsl = pake.DSL()
task = __dsl.task
phony = __dsl.phony
sh = pake.sh
rm = pake.rm


all_files = sh("git ls-files -z", stdout=subprocess.PIPE).stdout.split("\0")
tests_sh_files = [path for path in all_files if path.startswith(f"tests{os.path.sep}") and path.endswith(".sh")]
tests_py_files = [path for path in all_files if path.startswith(f"tests{os.path.sep}") and path.endswith(".py")]
pake_py_files = [path for path in all_files if path.startswith(f"pake{os.path.sep}") and path.endswith(".py")]


def let():
    phony("default", ["check"])

    def let():
        for test_sh in tests_sh_files:
            test_sh_done = test_sh + ".done"
            phony("check", [test_sh_done])

            @task([test_sh_done], [test_sh] + pake_py_files)
            def _(j):
                sh(f"""
                {j.ds[0]}
                touch {j.ts[0]}
                """)
    let()

    def let():
        for test_py in tests_py_files:
            test_py_done = test_py + ".done"
            phony("check", [test_py_done])

            @task([test_py_done], [test_py] + pake_py_files)
            def _(j):
                sh(f"""
                {os.environ["PYTHON"]} {j.ds[0]}
                touch {j.ts[0]}
                """)
    let()
let()


if __name__ == '__main__':
    __dsl.main(sys.argv)
