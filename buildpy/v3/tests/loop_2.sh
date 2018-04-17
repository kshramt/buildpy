#!/bin/bash
# @(#) -j

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
import time

import buildpy.v3


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.v3.DSL()
file = dsl.file
phony = dsl.phony
loop = dsl.loop
sh = dsl.sh
rm = dsl.rm


@loop((i for i in range(2)), [j for j in range(3)])
def _(i, j):
    phony("all", [f"{i}_{j}"])
    @phony(f"{i}_{j}", [])
    def _(j):
        print(j.ts[0])


if __name__ == '__main__':
    dsl.main(sys.argv)
EOF

"$PYTHON" build.py | sort >| actual
cat <<EOF >| expected
0_0
0_1
0_2
1_0
1_1
1_2
EOF
git diff --color-words --no-index --word-diff expected actual
