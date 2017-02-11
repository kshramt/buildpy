#!/bin/bash

# set -xv
set -o nounset
set -o errexit
set -o pipefail
set -o noclobber

export IFS=$' \t\n'
export LANG=en_US.UTF-8
umask u=rwx,g=,o=


readonly tmp_dir="$(mktemp -d)"

finalize(){
   rm -fr "$tmp_dir"
}

trap finalize EXIT


cd "$tmp_dir"


cat <<EOF > pakefile.py
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


def let():
    phony("default", [])
let()


if __name__ == '__main__':
    __dsl.main(sys.argv)
EOF


cat <<EOF > expect
EOF

"$PYTHON" pakefile.py > actual

colordiff expect actual
