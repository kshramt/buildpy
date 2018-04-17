#!/bin/bash
# @(#) --dry-run

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

import buildpy.vx


def _setup_logger():
    import logging
    logger = logging.getLogger()
    hdl = logging.StreamHandler(sys.stderr)
    hdl.setFormatter(logging.Formatter("%(levelname)s\t%(process)d\t%(asctime)s\t%(filename)s\t%(funcName)s\t%(lineno)d\t%(message)s"))
    logger.addHandler(hdl)
    logger.setLevel(logging.DEBUG)
    return logger


logger = _setup_logger()


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


dsl = buildpy.vx.DSL(sys.argv)
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm


phony("all", ["a"], desc="Default target")

@file(["a"], ["b"], desc="Test 2")
def _(j):
    sh("touch " + " ".join(j.ts))

@file(["b"], ["c", "d"], desc="Test 1")
def _(j):
    sh("touch " + " ".join(j.ts))

@file(["d"], ["e"])
def _(j):
    sh("touch " + " ".join(j.ts))


if __name__ == '__main__':
    dsl.run()
EOF

cat <<EOF > expect
d
	e

b
	c
	d

a
	b

all
	a

EOF

touch c e

"$PYTHON" build.py -n > actual

git diff --color-words --no-index --word-diff expect actual
