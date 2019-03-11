#!/bin/bash
# @(#) Handle missing targets of child tasks gracefully

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

import logging
import os
import time
import sys

import buildpy.v6


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


dsl = buildpy.v6.DSL(sys.argv)
logger = _setup_logger(dsl.args.log)
file = dsl.file
phony = dsl.phony
loop = dsl.loop
sh = dsl.sh
rm = dsl.rm


phony("all", ["x", "a"], desc="The default target")


@file(["x"], ["y"])
def _(j):
    sh(f"""
    touch {j.ts[0]}
    """)


@file(["y"], [])
def _(j):
    pass


@file(["a"], ["b"])
def _(j):
    sh(f"""
    touch {j.ts[0]}
    """)


@file(["b"], [])
def _(j):
    time.sleep(3)


if __name__ == '__main__':
    t1 = time.time()
    try:
        dsl.run()
    except:
        pass
    t2 = time.time()
    assert t2 - t1 >= 3
EOF


timeout 16 "$PYTHON" build.py -j2 -k --log=critical
# timeout 16 "$PYTHON" build.py -j2 # should fail
