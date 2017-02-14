#!/bin/bash
# @(#) depth first search

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


cat <<EOF > build.py
#!/usr/bin/python

import os
import subprocess
import sys
import time

import buildpy


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


__dsl = buildpy.v1.DSL()
file = __dsl.file
phony = __dsl.phony
sh = __dsl.sh
rm = __dsl.rm


@phony("all", ["x1", "x2"])
def _(j):
    print(j.ts[0])

@phony("x1", ["x11"])
def _(j):
    print(j.ts[0])

@phony("x2", ["x22"])
def _(j):
    print(j.ts[0])

@phony("x11", ["x111"])
def _(j):
    print(j.ts[0])

@phony("x22", ["x222"])
def _(j):
    print(j.ts[0])

@phony("x111", [])
def _(j):
    print(j.ts[0])

@phony("x222", [])
def _(j):
    print(j.ts[0])


if __name__ == '__main__':
    __dsl.main(sys.argv)
EOF

cat <<EOF > expect
x222
x22
x2
x111
x11
x1
all
EOF

"$PYTHON" build.py > actual

colordiff -u expect actual
