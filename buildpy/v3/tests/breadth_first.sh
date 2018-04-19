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
import sys

import buildpy.v3


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.v3.DSL()
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm


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
    dsl.main(sys.argv)
EOF

cat <<EOF > expect
x111
x222
x11
x22
x1
x2
all
EOF

"$PYTHON" build.py > actual

git diff --color-words --no-index --word-diff expect actual
