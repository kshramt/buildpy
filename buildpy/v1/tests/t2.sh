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


nx = 7
ny = 7
nz = 7
dt = 0.1

xs = ["x" + str(i) for i in range(nx)]
ys = ["y" + str(i) for i in range(ny)]
zs = ["z" + str(i) for i in range(nz)]


@phony("all", xs)
def _(j):
    # print(j)
    time.sleep(dt)

for x in xs:
    @phony(x, [x + y for y in ys])
    def _(j):
        # print(j)
        time.sleep(dt)

    for y in ys:
        @phony(x + y, [x + y + z for z in zs])
        def _(j):
            # print(j)
            time.sleep(dt)

        for z in zs:
            @phony(x + y + z, [])
            def _(j):
                # print(j)
                time.sleep(dt)


if __name__ == '__main__':
    t1 = time.time()
    __dsl.main(sys.argv)
    t2 = time.time()
    assert t2 - t1 < dt*(1 + nx*(1 + ny*(1 + nz)))/10
EOF

"$PYTHON" build.py -j20
