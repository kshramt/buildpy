#!/bin/bash
# @(#) --dry-run

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

import buildpy


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


__dsl = buildpy.v1.DSL()
file = __dsl.file
phony = __dsl.phony
sh = __dsl.sh
rm = __dsl.rm


phony("all", "a", desc="Default target")

@file("a", "b", desc="Test 2")
def _(j):
    sh("touch " + " ".join(j.ts))

@file("b", ["c", "d"], desc="Test 1")
def _(j):
    sh("touch " + " ".join(j.ts))

@file("d", "e")
def _(j):
    sh("touch " + " ".join(j.ts))


if __name__ == '__main__':
    __dsl.main(sys.argv)
EOF

cat <<EOF > expect
d
	e

b
	c
	d

a
	b

all
	a

EOF

touch c e

"$PYTHON" build.py -n > actual

colordiff -u expect actual
