#!/usr/bin/python3

import doctest
import os
import sys
import tempfile

import buildpy.v6


def main(argv):
    for mod in [
            buildpy.v6,
            buildpy.v6._convenience,
            buildpy.v6._log,
            buildpy.v6._tval,
            buildpy.v6.exception,
            buildpy.v6.resource,
    ]:
        result = doctest.testmod(mod)
        if result.failed > 0:
            exit(mod)

    @buildpy.v6.DSL.let
    def _():
        puri = buildpy.v6.DSL.uriparse("a/b;c;d?e=f#gh")
        assert puri.uri == "a/b;c;d?e=f#gh", puri.uri
        assert puri.scheme == "file", puri.scheme
        assert puri.netloc == "localhost", puri.netloc
        assert puri.path == "a/b", puri.path
        assert puri.params == "c;d", puri.params
        assert puri.query == "e=f", puri.query
        assert puri.fragment == "gh", puri.fragment

    @buildpy.v6.DSL.let
    def _():
        ctx = buildpy.v6._TaskContext()
        outs = []

        @ctx.task
        def f(self):
            outs.append(1)
            yield
            outs.append(2)
            yield
            outs.append(3)

        @ctx.task
        def g(self):
            yield self.wait(f)
            outs.append(4)

        @ctx.task
        def h(self):
            yield self.wait(g)

        h.wait()
        assert outs == [1, 2, 3, 4], outs

    @buildpy.v6.DSL.let
    def _():
        s = buildpy.v6._tval.TSet()
        s.add(s.add(s.add(1)))
        assert len(s) == 2
        s.remove(s.remove(1))
        assert len(s) == 0

    @buildpy.v6.DSL.let
    def _():
        s = buildpy.v6.DSL.serialize(None)
        assert s == "n", s
        s = buildpy.v6.DSL.serialize(1)
        assert s == "i1_", s
        s = buildpy.v6.DSL.serialize(2)
        assert s == "i2_", s
        s = buildpy.v6.DSL.serialize(1.234e89)
        assert s == "fi22_0x1.f041b8a7a54f1p+295", s
        s = buildpy.v6.DSL.serialize([1.234e89])
        assert s == "li1_fi22_0x1.f041b8a7a54f1p+295", s
        s = buildpy.v6.DSL.serialize([1.234e89, 32])
        assert s == "li2_fi22_0x1.f041b8a7a54f1p+295i32_", s
        s = buildpy.v6.DSL.serialize({1: 1.234e89, 99: ["b", 4], 3.2: {"p": 9, "r": -9831.98773}})
        assert s == "di3_fi20_0x1.999999999999ap+1di2_si1_pi9_si1_rfi22_-0x1.333fe6defc7a4p+13i1_fi22_0x1.f041b8a7a54f1p+295i99_li2_si1_bi4_", s
        assert buildpy.v6.DSL.serialize(dict(a=1, b=2, c=3)) == buildpy.v6.DSL.serialize(dict(c=3, b=2, a=1)) == buildpy.v6.DSL.serialize(dict(b=2, a=1, c=3))

    @buildpy.v6.DSL.let
    def _():
        def comp(p1, p2):
            p1, p2 = os.path.realpath(p1), os.path.realpath(p2)
            assert p1 == p2, (p1, p2)

        @buildpy.v6.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            tmp2box = []
            @buildpy.v6.DSL.cd(tmp1)
            def _():
                tmp2box.append(os.getcwd())
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2box[0])
        @buildpy.v6.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            tmp2box = []
            @buildpy.v6.DSL.cd(tmp1)
            def _(c):
                tmp2box.append(os.getcwd())
                comp(c.old, tmp0)
                comp(c.new, tmp2box[0])
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2box[0])
        @buildpy.v6.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            with buildpy.v6.DSL.cd(tmp1):
                tmp2 = os.getcwd()
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2)
        @buildpy.v6.DSL.let
        def _():
            tmp0 = os.getcwd()
            tmp1 = tempfile.gettempdir()
            with buildpy.v6.DSL.cd(tmp1) as c:
                tmp2 = os.getcwd()
                comp(c.old, tmp0)
                comp(c.new, tmp2)
            tmp3 = os.getcwd()
            comp(tmp0, tmp3)
            comp(tmp1, tmp2)


if __name__ == "__main__":
    main(sys.argv)
