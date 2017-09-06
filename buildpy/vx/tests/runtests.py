#!/usr/bin/python

import doctest
import sys

import buildpy.vx


def main(argv):
    doctest.testmod(buildpy.vx, raise_on_error=True)
    # doctest.testmod(buildpy.vx)

    @buildpy.vx.DSL.let
    def _():
        s = buildpy.vx._TSet()
        s.add(s.add(s.add(1)))
        assert len(s) == 2
        s.remove(s.remove(1))
        assert len(s) == 0


if __name__ == '__main__':
    main(sys.argv)
