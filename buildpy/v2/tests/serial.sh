#!/bin/bash
# @(#) Jobs with `serial=True` should not run in parallel

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

import datetime
import os
import sys
import time

import buildpy.v2


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.v2.DSL(use_hash=True)
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm
loop = dsl.loop


@file(["aa"], ["bb"])
def _(j):
    pass


@loop(["x", "y", "z"])
def _(x):
    ts = [f"{x}1", f"{x}2", f"{x}3"]
    @file(ts, [f"{x}0"], serial=True)
    def _(j):
        time.sleep(1)
        sh(f"touch {' '.join(j.ts)}")

    @loop(ts)
    def _(y):
        t = "p" + y
        @file([t], [y])
        def _(j):
            time.sleep(1)
            sh(f"touch {j.ts[0]}")
        phony("all", [t])


if __name__ == '__main__':
    t1 = datetime.datetime.now()
    dsl.main(sys.argv)
    t2 = datetime.datetime.now()
    dt = (t2 - t1)/datetime.timedelta(seconds=1)
    assert 3.5 < dt < 4.5, dt
EOF

touch x0 y0 z0
"$PYTHON" build.py -j1000 2> /dev/null
