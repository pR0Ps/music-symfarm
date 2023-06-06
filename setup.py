#!/usr/bin/env python

from setuptools import setup
import os.path


try:
    DIR = os.path.abspath(os.path.dirname(__file__))
    with open(os.path.join(DIR, "README.md"), encoding='utf-8') as f:
        long_description = f.read()
except Exception:
    long_description=None


setup(
    name="music-symfarm",
    version="0.0.1",
    description="Uses the tags stored in music files to create and manage a symlink farm pointing to them",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/pR0Ps/music-symfarm",
    project_urls={
        "Source": "https://github.com/pR0Ps/music-symfarm",
    },
    license="MPLv2",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
        "Topic :: Multimedia :: Sound/Audio",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
    ],
    packages=["music_symfarm"],
    package_data={
        "music_symfarm": [
            "defaults.yaml"
        ]
    },
    python_requires=">=3.9",
    install_requires=[
        "pytaglib>=1.4.1,<3.0.0",
        "pyyaml>=5.3,<7.0",
        "setuptools>=23.0.0"
    ],
    entry_points={
        "console_scripts": [
            "music-symfarm=music_symfarm.__main__:main"
        ]
    }
)
