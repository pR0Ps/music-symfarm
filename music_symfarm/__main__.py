#!/usr/bin/env python

import argparse
import functools
import logging
import sys

import pkg_resources
import yaml

from music_symfarm import _make_symfarm


__log__ = logging.getLogger(__name__)


DEFAULTS = yaml.safe_load(pkg_resources.resource_stream(__name__, "defaults.yaml"))

# Backport from Python 3.9
# https://github.com/python/cpython/commit/6a517c674907c195660fa9178a7b561de49cc721
class BooleanOptionalAction(argparse.Action):
    def __init__(
        self,
        option_strings,
        dest,
        default=None,
        type=None,
        choices=None,
        required=False,
        help=None,
        metavar=None,
    ):
        _option_strings = []
        for option_string in option_strings:
            _option_strings.append(option_string)

            if option_string.startswith("--"):
                option_string = "--no-" + option_string[2:]
                _option_strings.append(option_string)

        super().__init__(
            option_strings=_option_strings,
            dest=dest,
            nargs=0,
            default=default,
            type=type,
            choices=choices,
            required=required,
            help=help,
            metavar=metavar,
        )

    def __call__(self, parser, namespace, values, option_string=None):
        if option_string in self.option_strings:
            setattr(namespace, self.dest, not option_string.startswith("--no-"))


def add_argument(parser, name, *, help, **kwargs):
    """Add an argument with it's default listed in the help text"""
    dest = kwargs.get("dest", name.lstrip("-").replace("-", "_"))
    default = DEFAULTS.get("options", {}).get(dest)
    if default is not None:
        help = "{} (default: {})".format(help, default)

    parser.add_argument(name, help=help, **kwargs)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a directory of symlinks based solely on music tags"
    )

    add_arg = functools.partial(add_argument, parser)

    add_arg("music_dir", help="Music source directory")
    add_arg("link_dir", help="Directory where the symlinks will be created")
    add_arg(
        "--conf",
        type=argparse.FileType("r"),
        help="A config file to override default settings",
    )
    add_arg(
        "--clean",
        action=BooleanOptionalAction,
        help="Clean the link directory of broken links and empty directories?",
    )
    add_arg(
        "--rescan-existing",
        action=BooleanOptionalAction,
        help="Rescan files that already have links pointing to them?",
    )
    add_arg(
        "--relative-links",
        action=BooleanOptionalAction,
        help="Use relative paths for links instead of absolute?",
    )
    add_arg(
        "--log",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: %(default)s)",
    )
    return vars(parser.parse_args())


def get_merged_configs(fp):
    merged = DEFAULTS.copy()
    if fp:
        conf = yaml.safe_load(fp)
        for k, v in conf.items():
            curr = merged.get(k)
            # Merge dicts, set everything else
            if curr is not None and isinstance(curr, dict) and isinstance(v, dict):
                merged[k].update(v)
            else:
                merged[k] = v
    return merged


def main():
    try:
        cli_args = parse_args()

        # Get merged config and override options from CLI ones
        config = get_merged_configs(cli_args.pop("conf", None))
        for k, v in cli_args.items():
            if v is not None:
                config["options"][k] = v

        opts = config.pop("options")
        level = opts.pop("log")
        logging.basicConfig(
            level=level, format="[%(levelname)8s] %(asctime)-15s %(message)s"
        )

        _make_symfarm(**opts, **config)
    except Exception as e:
        __log__.exception("%s: %s", type(e).__name__, str(e))
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
