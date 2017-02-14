from setuptools import setup

setup(
    name="buildpy",
    version="1.1.0",
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
