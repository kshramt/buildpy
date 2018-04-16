#!/bin/bash
# @(#) j.ty and j.dy

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


cat <<EOF > "$tmp_dir"/build.py
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


phony("all", ["endpoint.done"], dy=["later"])


@file(["endpoint"], ["src"])
def _(j):
    sh(f"touch {j.ts[0]}")


@file(["endpoint.done"], ["endpoint"], ty=["many"])
def this(j):
    many = [1, 2, 3]
    this.set_ty("many", [f"x{i}" for i in many])  # Dead lock?
    for i in many:
        sh(f"touch x{i}")

        @file([f"p{i}"], [f"x{i}"])
        def _(j):
            sh(f"touch {j.ts[0]}")

    @file([f"ppp"], [f"p{i}" for i in many])
    def _(j):
        sh(f"touch {j.ts[0]}")

    dsl.job_of_target["all"].set_dy("later", ["ppp"])


if __name__ == '__main__':
    dsl.run()
    print(dsl.dependencies_json())
    # print(dsl.dependencies_dot())
EOF


cd "$tmp_dir"

cat <<EOF > expected
[
  {
    "dy": {
      "_ds": [
        "endpoint.done"
      ],
      "later": [
        "ppp"
      ]
    },
    "ty": {
      "_ts": [
        "all"
      ]
    }
  },
  {
    "dy": {
      "_ds": [
        "src"
      ]
    },
    "ty": {
      "_ts": [
        "endpoint"
      ]
    }
  },
  {
    "dy": {
      "_ds": [
        "endpoint"
      ]
    },
    "ty": {
      "_ts": [
        "endpoint.done"
      ],
      "many": [
        "x1",
        "x2",
        "x3"
      ]
    }
  },
  {
    "dy": {
      "_ds": [
        "x1"
      ]
    },
    "ty": {
      "_ts": [
        "p1"
      ]
    }
  },
  {
    "dy": {
      "_ds": [
        "x2"
      ]
    },
    "ty": {
      "_ts": [
        "p2"
      ]
    }
  },
  {
    "dy": {
      "_ds": [
        "x3"
      ]
    },
    "ty": {
      "_ts": [
        "p3"
      ]
    }
  },
  {
    "dy": {
      "_ds": [
        "p1",
        "p2",
        "p3"
      ]
    },
    "ty": {
      "_ts": [
        "ppp"
      ]
    }
  },
  {
    "dy": {
      "_ds": []
    },
    "ty": {
      "_ts": [
        "src"
      ]
    }
  }
]
EOF

touch src
"$PYTHON" build.py 2> /dev/null | jq . > actual

git diff --color-words --no-index --word-diff expected actual
