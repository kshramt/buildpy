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

import pake.v1 as pakevx


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


__dsl = pakevx.DSL()
file = __dsl.file
phony = __dsl.phony
sh = pakevx.sh
rm = pakevx.rm


phony("all", ["check"], desc="Default target")
phony("check", ["t1.done", "t2.done"], desc="Run tests")

@file("t2.done", ["t2"], desc="Test 2")
def _(j):
    pass

@file("t1.done", ["t1"], desc="Test 1")
def _(j):
    pass

@file(["t2", "t1"], ["u2", "u1"])
def _(j):
    pass


if __name__ == '__main__':
    __dsl.main(sys.argv)
EOF

cat <<EOF > expect
all
	check

check
	t1.done
	t2.done

t1.done
	t1

t2
t1
	u2
	u1

t2.done
	t2

EOF

touch u1 u2

"$PYTHON" pakefile.py -P > actual

colordiff expect actual
