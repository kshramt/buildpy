#!/usr/bin/python

import doctest
import sys

import buildpy.v1


def main(argv):
    doctest.testmod(buildpy.v1, raise_on_error=True)
    # doctest.testmod(buildpy.v1)

    @buildpy.v1.DSL.let
    def _():
        s = buildpy.v1._TSet()
        s.add(s.add(s.add(1)))
        assert len(s) == 2
        s.remove(s.remove(1))
        assert len(s) == 0


if __name__ == '__main__':
    main(sys.argv)
