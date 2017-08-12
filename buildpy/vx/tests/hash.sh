#!/bin/bash
# @(#) Test hash-related code

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

import hashlib
import os
import sys

import buildpy.v1

a = bytearray(buildpy.v1.BUF_SIZE + 1)
b = b'abcdefg'
buildpy.v1._write_bytes("a", a)
buildpy.v1._write_bytes("b", b)

expected = hashlib.sha1(
    len(a).to_bytes(64, sys.byteorder)
    + a
    + len(b).to_bytes(64, sys.byteorder)
    + b
).hexdigest()
actual = buildpy.v1._hash_of_paths(["a", "b"]).hexdigest()

assert actual == expected
EOF

"$PYTHON" build.py
