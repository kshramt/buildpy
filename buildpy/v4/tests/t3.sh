#!/bin/bash
# @(#) -D

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

import buildpy.v4


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.v4.DSL()
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm


phony("all", ["check"], desc="Default target")
phony("check", ["t1", "t2"], desc="Run tests")
phony("t2", [], desc="Test 2")
phony("t1", [], desc="Test 1")


if __name__ == '__main__':
    dsl.main(sys.argv)
EOF


cat <<EOF > expect
all
	Default target
check
	Run tests
t1
	Test 1
t2
	Test 2
EOF

"$PYTHON" build.py -D > actual

git diff --color-words --no-index --word-diff expect actual
