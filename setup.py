import os

from setuptools import setup


def getversion():
    head = "from .v"
    tail = " import (\n"
    with open(os.path.join("buildpy", "__init__.py")) as fp:
        l = fp.readline()
    assert l.startswith(head)
    assert l.endswith(tail)
    vx = "v" + l[len(head):-len(tail)]
    head = '__version__ = "'
    tail = '"\n'
    with open(os.path.join("buildpy", vx, "__init__.py")) as fp:
        for l in fp:
            if l.startswith(head) and l.endswith(tail):
                return l[len(head):-len(tail)]
    raise Exception("__version__ not found")


setup(
    name="buildpy",
    version=getversion(),
    description="Make in Python",
    url="https://github.com/kshramt/buildpy",
    author="kshramt",
    license="GPLv3",
    packages=[
        "buildpy",
        "buildpy.v1",
    ],
    zip_safe=True,
)
