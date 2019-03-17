#!/bin/bash
# @(#) Check handling of missing dependencies (files)

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
import sys

import buildpy.vx


os.environ["SHELL"] = "/bin/bash"
os.environ["SHELLOPTS"] = "pipefail:errexit:nounset:noclobber"
os.environ["PYTHON"] = sys.executable


def _setup_logger():
    logger = logging.getLogger()
    hdl = logging.StreamHandler(sys.stderr)
    hdl.setFormatter(logging.Formatter("%(levelname)s %(process)d %(thread)d %(asctime)s %(filename)s %(lineno)d %(funcName)s %(message)s", "%y%m%d%H%M%S"))
    logger.addHandler(hdl)
    logger.setLevel(logging.DEBUG)
    return logger


logger = _setup_logger()


dsl = buildpy.vx.DSL(sys.argv, use_hash=False)
file = dsl.file
phony = dsl.phony
sh = dsl.sh
rm = dsl.rm


@phony("all", ["x"])
def _(j):
    pass


@file(["x"], ["y"])
def _(j):
    pass


if __name__ == '__main__':
    dsl.run()
EOF


timeout 10 sh -c 'if "$PYTHON" build.py 2> /dev/null ; then echo should fail; exit 1 ; fi'
