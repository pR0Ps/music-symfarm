#!/usr/bin/env python

import argparse
import logging
import sys

from music_symfarm import make_symfarm


__log__ = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description='Create a directory of symlinks based solely on music tags')
    parser.add_argument('music_dir',
                        help='Music source directory')
    parser.add_argument('link_dir',
                        help='Directory where the symlinks will be created')
    parser.add_argument('--clean', action='store_true',
                        help='Clean the link directory of broken links and empty directories')
    parser.add_argument('--rescan-existing', action='store_true',
                        help='Rescan files that already have links pointing to them')
    parser.add_argument("--relative-links", action='store_true',
                        help="Use relative paths for links (default: absolute paths)")
    parser.add_argument("--log", choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default="INFO",
                        help="Set the logging level (default: %(default)s)")
    return vars(parser.parse_args())


def main():
    try:
        args = parse_args()

        level = args.pop("log")
        logging.basicConfig(level=level, format="[%(levelname)8s] %(asctime)-15s %(message)s")

        make_symfarm(**args)
    except Exception as e:
        __log__.exception(str(e))
        sys.exit(1)
    else:
        sys.exit(0)
