import os

from setuptools import setup


def getversion():
    head = '__version__ = "'
    tail = '"\n'
    with open(os.path.join("buildpy", "vx", "__init__.py")) as fp:
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
        "buildpy.v1",
        "buildpy.v2",
        "buildpy.v3",
        "buildpy.v4",

        "buildpy.v5",
        "buildpy.v5._convenience",
        "buildpy.v5._log",
        "buildpy.v5._tval",
        "buildpy.v5.exception",
        "buildpy.v5.resource",

        "buildpy.vx",
        "buildpy.vx._convenience",
        "buildpy.vx._log",
        "buildpy.vx._tval",
        "buildpy.vx.exception",
        "buildpy.vx.resource",
    ],
    install_requires=[
        "google-cloud-bigquery",
        "google-cloud-storage",
        "psutil",
    ],
    zip_safe=True,
)
