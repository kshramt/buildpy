#!/bin/bash
# @(#) -P

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

import buildpy.v1


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.v1.DSL()
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm


phony("all", "check", desc="Default target")
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
    dsl.main(sys.argv)
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

"$PYTHON" build.py -P > actual

git diff --color-words --no-index --word-diff expect actual
