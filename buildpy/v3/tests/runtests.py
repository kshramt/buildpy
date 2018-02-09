#!/usr/bin/python

import doctest
import os
import sys
import tempfile

import buildpy.v3


def main(argv):
    result = doctest.testmod(buildpy.v3)
    if result.failed > 0:
        exit(1)

    @buildpy.v3.DSL.let
    def _():
        s = buildpy.v3._TSet()
        s.add(s.add(s.add(1)))
        assert len(s) == 2
        s.remove(s.remove(1))
        assert len(s) == 0

    @buildpy.v3.DSL.let
    def _():
        s = buildpy.v3._serialize(1)
        assert s == b"i\x01\x00\x00\x00\x00\x00\x00\x00", s
        s = buildpy.v3._serialize(2)
        assert s == b"i\x02\x00\x00\x00\x00\x00\x00\x00", s
        s = buildpy.v3._serialize(1.234e89)
        assert s == b"f\xf1Tz\x8a\x1b\x04oR", s
        s = buildpy.v3._serialize([1.234e89])
        assert s == b"l\x01\x00\x00\x00\x00\x00\x00\x00f\xf1Tz\x8a\x1b\x04oR", s
        s = buildpy.v3._serialize([1.234e89, 32])
        assert s == b"l\x02\x00\x00\x00\x00\x00\x00\x00f\xf1Tz\x8a\x1b\x04oRi \x00\x00\x00\x00\x00\x00\x00", s
        s = buildpy.v3._serialize({1: 1.234e89, 99: ["b", 4], 3.2: {"p": 9, "r": -9831.98773}})
        assert s == b"d\x03\x00\x00\x00\x00\x00\x00\x00i\x01\x00\x00\x00\x00\x00\x00\x00f\xf1Tz\x8a\x1b\x04oRf\x9a\x99\x99\x99\x99\x99\t@d\x02\x00\x00\x00\x00\x00\x00\x00s\x01\x00\x00\x00\x00\x00\x00\x00pi\t\x00\x00\x00\x00\x00\x00\x00s\x01\x00\x00\x00\x00\x00\x00\x00rf\xa4\xc7\xefm\xfe3\xc3\xc0ic\x00\x00\x00\x00\x00\x00\x00l\x02\x00\x00\x00\x00\x00\x00\x00s\x01\x00\x00\x00\x00\x00\x00\x00bi\x04\x00\x00\x00\x00\x00\x00\x00", s
        assert buildpy.v3._serialize(dict(a=1, b=2, c=3)) == buildpy.v3._serialize(dict(c=3, b=2, a=1)) == buildpy.v3._serialize(dict(b=2, a=1, c=3))

    @buildpy.v3.DSL.let
    def _():
        def comp(p1, p2):
            p1, p2 = os.path.realpath(p1), os.path.realpath(p2)
            assert p1 == p2, (p1, p2)

        @buildpy.v3.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            tmp2box = []
            @buildpy.v3.DSL.cd(tmp1)
            def _():
                tmp2box.append(os.getcwd())
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2box[0])
        @buildpy.v3.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            tmp2box = []
            @buildpy.v3.DSL.cd(tmp1)
            def _(c):
                tmp2box.append(os.getcwd())
                comp(c.old, tmp0)
                comp(c.new, tmp2box[0])
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2box[0])
        @buildpy.v3.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            with buildpy.v3.DSL.cd(tmp1):
                tmp2 = os.getcwd()
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2)
        @buildpy.v3.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            with buildpy.v3.DSL.cd(tmp1) as c:
                tmp2 = os.getcwd()
                comp(c.old, tmp0)
                comp(c.new, tmp2)
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2)


if __name__ == "__main__":
    main(sys.argv)
