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
#!/usr/bin/python3

import os
import sys

import buildpy.vx


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.vx.DSL(sys.argv, use_hash=False)
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm


@file(dict(a="a",b="b"), dict(p="p", q=["q1", "q2"]))
def _(j):
    sh(
        f"""
        cat "{j.ds["p"]}" >| "{j.ts["a"]}"
        cat "{j.ds["q"][0]}" >| "{j.ts["b"]}"
        """
    )


@file(["p", [dict(q1="q1", q2=["q2"])]], dict())
def _(j):
    sh(
        f"""
        touch "{j.ts[0]}"
        touch "{j.ts[1][0]["q1"]}"
        touch "{j.ts[1][0]["q2"][0]}"
        """
    )


phony("all", ["b"])


if __name__ == '__main__':
    dsl.run()
EOF

"$PYTHON" build.py 2> /dev/null
cat <<EOF > expected
a
b
	p
	q1
	q2

all
	b

p
q1
q2

EOF

"$PYTHON" build.py -P > actual
git diff --color-words --no-index --word-diff expected actual
