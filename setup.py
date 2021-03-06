#!/usr/bin/env python

from setuptools import setup

setup(
    name="music-symfarm",
    version="0.0.1",
    description="Uses the tags stored in music files to create and manage a symlink farm pointing to them",
    url="https://github.com/pR0Ps/music-symfarm",
    license="MPLv2",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
    ],
    packages=["music_symfarm"],
    package_data={
        "music_symfarm": [
            "defaults.yaml"
        ]
    },
    install_requires=[
        "pytaglib>=1.4.1,<2.0.0",
        "pyyaml>=5.3<6.0",
        "setuptools>=23.0.0"
    ],
    entry_points={
        "console_scripts": [
            "music-symfarm=music_symfarm.__main__:main"
        ]
    }
)
