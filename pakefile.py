#!/usr/bin/python

import os
import subprocess
import sys

import pake
# import scipy as sp
# import matplotlib.pyplot as plt
# import pandas as pd


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


__dsl = pake.DSL()
task = __dsl.task
phony = __dsl.phony


def sh(s, stdout=None):
    print(s, file=sys.stderr)
    return subprocess.run(
        s,
        shell=True,
        check=True,
        env=os.environ,
        executable="/bin/bash",
        stdout=stdout,
        universal_newlines=True,
    )


all_files = sh("git ls-files -z", stdout=subprocess.PIPE).stdout.split("\0")


def let():
    def let():
        for test_sh in [path for path in all_files if path.startswith(f"tests{os.path.sep}") and path.endswith(".sh")]:
            test_sh_done = test_sh + ".done"
            phony("default", [test_sh_done])

            @task([test_sh_done], [test_sh])
            def _(j):
                sh(f"{j.ds[0]} && touch {j.ts[0]}")
    let()
let()


if __name__ == '__main__':
    __dsl.main(sys.argv)
