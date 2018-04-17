#!/bin/bash
# @(#) `use_hash` with contents change

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


@phony("all", ["x"])
def _(j):
    print(j.ts[0], j.ds[0])

@file(["x"], ["y", "z"], use_hash=True)
def _(j):
    print(j.ts[0], j.ds[0], j.ds[1])
    sh("touch " + j.ts[0])


if __name__ == '__main__':
    dsl.main(sys.argv)
EOF

cat <<EOF > expect.1
x y z
all x
x y z
all x
all x
==
all x
ooo
all x
EOF

cat <<EOF > expect.2
touch x
touch x
==
ooo
EOF

{
   # initial
   echo y >| y
   echo z >| z
   "$PYTHON" build.py
   # contents have changed
   sleep 1.1
   echo more >> y
   "$PYTHON" build.py
   # contents have not changed
   sleep 1.1
   touch y
   "$PYTHON" build.py
   echo ==
   echo == 1>&2
   # Run-again
   "$PYTHON" build.py
   echo ooo
   echo ooo 1>&2
   # contents have changed, but timestamp is older
   sleep 1.1
   echo more more >> y
   touch --date 1970-01-01 y
   "$PYTHON" build.py
} 1> actual.1 2> actual.2

git diff --color-words --no-index --word-diff expect.1 actual.1
git diff --color-words --no-index --word-diff expect.2 actual.2
