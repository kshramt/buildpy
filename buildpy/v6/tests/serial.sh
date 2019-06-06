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
#!/usr/bin/python3

import os
import sys
import time

import buildpy.v6


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.v6.DSL(sys.argv)
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm
loop = dsl.loop


@file(["aa"], ["bb"])
def _(j):
    pass


all_jobs = []


@loop(["x", "y", "z"])
def _(x):
    ts = [f"{x}1", f"{x}2", f"{x}3"]
    @file(ts, [f"{x}0"], serial=True)
    def _(j):
        time.sleep(1)
        sh(f"touch {' '.join(j.ts)}", quiet=True)

    @loop(ts)
    def _(y):
        t = "p" + y
        @file([t], [y])
        def _(j):
            time.sleep(1)
            sh(f"touch {j.ts[0]}", quiet=True)
        all_jobs.append(t)


phony("all", all_jobs)


if __name__ == '__main__':
    t1 = time.time()
    dsl.run()
    t2 = time.time()
    dt = t2 - t1
    assert 3.5 < dt < 4.5, dt
EOF

touch x0 y0 z0
"$PYTHON" build.py -j1000
