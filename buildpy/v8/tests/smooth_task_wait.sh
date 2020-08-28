#!/bin/bash
# @(#) Tasks should run smoothly

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
import sys
import time

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


letters = ["a", "b", "c", "d", "e", "f", "g", "h", "i"]


phony("all", [f"{c}0" for c in letters], desc="The default target")


@loop(letters)
def _(c):
    @phony(f"{c}0", [f"{c}1"])
    def _(j):
        # logger.warning("begin %s", j)
        time.sleep(2)

    @phony(f"{c}1", [])
    def _(j):
        # logger.warning("begin %s", j)
        time.sleep(2)


if __name__ == '__main__':
    t1 = time.time()
    dsl.run()
    t2 = time.time()
    assert t2 - t1 < 8
EOF


timeout 16 "$PYTHON" build.py -j6
