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

import buildpy.v1


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.v1.DSL()
file = dsl.file
phony = dsl.phony
loop = dsl.loop
sh = dsl.sh
rm = dsl.rm


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


@loop(xs)
def _(x):
    @phony(x, [x + y for y in ys])
    def _(j):
        # print(j)
        time.sleep(dt)

    @loop(ys)
    def _(y):
        @phony(x + y, [x + y + z for z in zs])
        def _(j):
            # print(j)
            time.sleep(dt)

        @loop(zs)
        def _(z):
            @phony(x + y + z, [])
            def _(j):
                # print(j)
                time.sleep(dt)


if __name__ == '__main__':
    t1 = time.time()
    dsl.main(sys.argv)
    t2 = time.time()
    assert t2 - t1 < dt*(1 + nx*(1 + ny*(1 + nz)))/10
EOF

"$PYTHON" build.py -j20
