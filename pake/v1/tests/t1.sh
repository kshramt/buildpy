#!/bin/bash
# @(#) smoke test

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

import pake.v1 as pakevx


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


__dsl = pakevx.DSL()
file = __dsl.file
phony = __dsl.phony
sh = pakevx.sh
rm = pakevx.rm


def let():
    phony("all", [], desc="Default target")
let()


if __name__ == '__main__':
    __dsl.main(sys.argv)
EOF


cat <<EOF > expect
EOF

"$PYTHON" pakefile.py > actual

colordiff expect actual
