#!/usr/bin/python

import doctest
import sys

import buildpy.vx


def main(argv):
    result = doctest.testmod(buildpy.vx)
    if result.failed > 0:
        exit(1)

    @buildpy.vx.DSL.let
    def _():
        s = buildpy.vx._TSet()
        s.add(s.add(s.add(1)))
        assert len(s) == 2
        s.remove(s.remove(1))
        assert len(s) == 0


if __name__ == '__main__':
    main(sys.argv)
