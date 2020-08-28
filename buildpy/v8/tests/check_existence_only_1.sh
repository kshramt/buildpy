#!/bin/bash
# @(#) Check if `dsl.check_existence_only` works fine.

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

import logging
import os
import time
import sys

import buildpy.v8


def _setup_logger(level):
    logger = logging.getLogger()
    hdl = logging.StreamHandler(sys.stderr)
    hdl.setFormatter(logging.Formatter("%(levelname)s %(process)d %(thread)d %(asctime)s %(filename)s %(lineno)d %(funcName)s %(message)s", "%y%m%d%H%M%S"))
    logger.addHandler(hdl)
    hdl.setLevel(getattr(logging, level))
    logger.setLevel(getattr(logging, level))
    return logger


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable
os.environ["PYTHONPATH"] = os.getcwd() + ((":" + os.environ["PYTHONPATH"]) if "PYTHONPATH" in os.environ else "")

python = os.environ["PYTHON"]


dsl = buildpy.v8.DSL(sys.argv)
logger = _setup_logger(dsl.args.log)
file = dsl.file
phony = dsl.phony
loop = dsl.loop
sh = dsl.sh
rm = dsl.rm


phony("should_success", ["a"])


@file(["a"], [dsl.check_existence_only("b")])
def _(j):
    sh(f"touch {j.ts[0]}")


@file(["b"], [])
def _(j):
    sh(f"touch {j.ts[0]}")


phony("should_fail", ["x"])


@file(["x"], ["y"])
def _(j):
    sh(f"touch {j.ts[0]}")


@file(["y"], [])
def _(j):
    sh(f": do nothing")


if __name__ == '__main__':
    dsl.run()
EOF


cat <<EOF > expected_1.2
touch b
touch a
EOF


{
   "$PYTHON" build.py should_success
   echo "update" >| b
   "$PYTHON" build.py should_success
} 2> actual_1.2
git diff --color-words --no-index --word-diff expected_1.2 actual_1.2


sh -c 'if "$PYTHON" build.py should_fail 2> /dev/null ; then echo should fail; exit 1 ; fi'
