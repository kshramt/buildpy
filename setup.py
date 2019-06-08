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

        "buildpy.v6",
        "buildpy.v6._convenience",
        "buildpy.v6._log",
        "buildpy.v6._tval",
        "buildpy.v6.exception",
        "buildpy.v6.resource",

        "buildpy.v7",
        "buildpy.v7._convenience",
        "buildpy.v7._log",
        "buildpy.v7._tval",
        "buildpy.v7.exception",
        "buildpy.v7.resource",

        "buildpy.v8",
        "buildpy.v8._convenience",
        "buildpy.v8._log",
        "buildpy.v8._tval",
        "buildpy.v8.exception",
        "buildpy.v8.resource",

        "buildpy.vx",
        "buildpy.vx._convenience",
        "buildpy.vx._log",
        "buildpy.vx._tval",
        "buildpy.vx.exception",
        "buildpy.vx.resource",
    ],
    install_requires=[
        "boto3",
        "google-cloud-bigquery",
        "google-cloud-storage",
        "psutil",
    ],
    classifiers=[
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)'
    ],
    data_files=[(".", ["LICENSE.txt"])],
    zip_safe=True,
)
