#!/bin/bash
# @(#) Test the automatic naming capability.

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


dsl = buildpy.vx.DSL(sys.argv)
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm


@file(["x"], ["y", "z"], use_hash=True, key=("y", 1), data=[dict(a=1)], auto=True)
def job(j):
    pass

@phony("all", job.ts_unique)
def pj(j):
    pass

assert set(dsl.jobs_of_key.keys()) == set([None, ("y", 1)]), dsl.jobs_of_key
assert len(dsl.jobs_of_key[None]) == 1, dsl.jobs_of_key
assert len(dsl.jobs_of_key[("y", 1)]) == 1, dsl.jobs_of_key
n_auto_prefix = len(dsl.args.auto_prefix)
pjds = [x[n_auto_prefix:] for x in pj.ds]
assert pjds == ["/69/9d41ceccb970de284da347cad339f1afcc2cd5/x"], pjds


if __name__ == '__main__':
    dsl.run()
EOF

{
   echo y >| y
   echo z >| z
   "$PYTHON" build.py
}
