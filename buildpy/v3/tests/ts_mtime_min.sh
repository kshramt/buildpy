#!/bin/bash
# @(#) The minimum mtime of targets should be compared with the maximum mtime of dependencies

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


phony("all", ["a", "b"], desc="Default target")

@file(["a", "b"], ["c", "d"])
def _(j):
    sh("sleep 2")
    sh("touch" + " " + " ".join(j.ts))

@file(["c", "d"], [])
def _(j):
    sh("touch" + " " + " ".join(j.ts))


if __name__ == '__main__':
    dsl.main(sys.argv)
EOF

cat <<EOF > expect.2
touch c d
sleep 2
touch a b
sleep 2
touch a b
EOF

touch u1 u2

{
   "$PYTHON" build.py
   touch --date 1970-01-01 b
   "$PYTHON" build.py
} 2> actual.2

git diff --color-words --no-index --word-diff expect.2 actual.2
