#!/usr/bin/env python

from collections import defaultdict
import contextlib
import itertools
import logging
import os
from pathlib import Path
import re
from re import error as RegexError
from string import Formatter
from typing import Pattern

import pkg_resources
import taglib
import yaml


# Tags that have to be the same for songs to be considered in the same album
ALBUM_TAGS = ("ALBUMARTIST", "ALBUM", "DATE")

# Regex to split a string on "/"s, excluding those found in {}'s (but not {{}}'s)
# NOTE: only works properly if the braces are balanced (ie. a valid format string)
RE_TEMPLATE_SLASHES = re.compile(r"/(?=(?:}}|[^}])*(?:(?:$|{[^{])))")

# Regex that matches non-escaped slashes
RE_SLASHES = re.compile(r"(?<!\\)/")

# Regex for matching regex-enabled format string replacements
RE_FORMAT_STR_REGEX = re.compile("{(.*?):/(.+?)" + RE_SLASHES.pattern + "(.+?)" + RE_SLASHES.pattern + "(.*)}")

# Regex for matching the regex portion of fields
RE_FORMAT_FIELD_EXPAND = re.compile("(.*)" + RE_SLASHES.pattern + "(.+?)" + RE_SLASHES.pattern)

__log__ = logging.getLogger(__name__)


class FieldRegexExpandError(ValueError):
    """Raised when trying to expand a non-regex match in a template"""

    def __init__(self, obj, field):
        super().__init__(
            f"Object '{obj}' from field '{field}' is not a re.Match and cannot be expanded",
            obj,
            field
        )


class RegexFormatter(Formatter):
    """A string formatter that does regex substitutions

    Adds the ability to use regular expressions to format objects:
     - Format: {0:/<pattern>/<repl>/<other formatting options>}
     - Ex: "{0:/(\w+) (\w+)/\2 \1/}".format("aaa bb") --> "bb aaa"
     - See https://docs.python.org/3/library/re.html#re.sub

    Adds the ability to expand re.Match objects with a template. Ex:
     - Format: {0/<template>/:<other formatting options>}
     - Ex: "{0/\2 \1/}".format(re.match(r"(\w+) (\w+)", "aaa, bb")) --> "bb aaa"
     - See https://docs.python.org/3/library/re.html#re.Match.expand

    To use the "/" character in the pattern or repl, escape it ("\\/")
    To use "{" or "}" characters in the pattern or repl, use "{{" or "}}"
    """
    def parse(self, format_string):
        # Allow for including curly braces in formats by changing them to
        # fullwidth versions for parsing and changing them back before yielding
        # them.
        esc = lambda x: x and x.replace("{{", "｛").replace("}}", "｝")
        unesc = lambda x: x and x.replace("｛", "{{").replace("｝", "}}")
        apply = lambda f, d, *n: tuple((x if i not in n else f(x)) for i, x in enumerate(d))
        escmatch = lambda m: "{{{}:/{}/{}/{}}}".format(*apply(esc, m.groups(), 1, 2))

        try:
            format_string = RE_FORMAT_STR_REGEX.sub(escmatch, format_string)
            for x in super().parse(format_string):
                yield apply(unesc, x, 2)
        except ValueError as e:
            raise ValueError(f"Failed to parse format string '{format_string}'") from e

    def format_field(self, value, format_spec):
        # Special cases:
        # Format an re.Match as its matched text instead of its repr
        if isinstance(value, re.Match):
            value = value.group(0)

        # Format None as "" instead of "None"
        if value is None:
            value = ""

        if format_spec.startswith("/"):
            # Split on non-escaped forward slashes
            _, pattern, repl, format_spec = RE_SLASHES.split(format_spec, maxsplit=3)
            # Convert any escaped slashes to regular slashes for the regex sub
            pattern, repl = (x.replace("\\/", "/") for x in (pattern, repl))
            value = re.sub(pattern, repl, value)

        return super().format_field(value, format_spec)

    def get_field(self, field_name, args, kwargs):
        # Handle templating of re.Match objects when using the {0/<template>/} syntax
        m = RE_FORMAT_FIELD_EXPAND.fullmatch(field_name)
        if m:
            field_name = m[1]

        obj, used_key = super().get_field(field_name, args, kwargs)

        if m:
            if not isinstance(obj, re.Match):
                raise FieldRegexExpandError(obj, used_key)
            # Convert any escaped slashes to regular slashes for the expansion
            obj = obj.expand(m[2].replace("\\/", "/"))

        return obj, used_key


REGEX_FORMATTER = RegexFormatter()


class Override:
    """Class to store and apply override data"""

    def __init__(self, rules, operations):
        self.rules = {k: self._make_rule(v) for k, v in rules.items()}
        self.operations = {k: self._make_operation(v) for k, v in operations.items()}
        self.debug = self.operations.pop("debug", False)

    @staticmethod
    def _make_operation(operation):
        """Only allow operations to set None/True/False/str (no numbers)"""
        if operation is None or isinstance(operation, bool):
            return operation
        return str(operation) or None

    @staticmethod
    def _make_rule(rule):
        """Parse a rule

        Assume rules are a regex if they start and end with '/'
        Returns None, a regex pattern object or a string
        """
        if rule is None:
            return rule
        rule = str(rule)
        if len(rule) > 2 and rule.startswith("/") and rule.endswith("/"):
            return re.compile(rule[1:-1])
        else:
            return rule

    @staticmethod
    def _rule_match(rule, data):
        """Return the match object/text if matched or False"""
        # Use False instead of None because None is a valid option
        if isinstance(rule, Pattern) and data is not None:
            return rule.fullmatch(data) or False
        elif rule == data:
            return data
        return False

    def matches(self, tags, *, tagmap):
        """Check if this override should be applied, given the provided tags

        Returns a dict of rule -> match if the override matches, otherwise None
        """
        ret = {}
        for tag_name, rule in self.rules.items():
            data = get_tag(tag_name, tags, tagmap=tagmap)
            ret[tag_name] = self._rule_match(rule, data)
            if ret[tag_name] is False:
                return None
        return ret

    def apply(self, tags, *, tagmap, fallbacks):
        """Apply the overrides to the tags (modifies tags in-place)"""
        matched = self.matches(tags, tagmap=tagmap)
        if matched:
            if self.debug:
                __log__.info("Song '%s' matched override %r", tags["abspath"], self)
            for k, v in self.operations.items():
                # Apply any formatting if the target is a string
                # (treat empty string as None)
                # Since path_template is meant to be a template, don't format it
                if k != "path_template" and isinstance(v, str):

                    # Override tags with matched information when formatting
                    # The matched information will either be the same as the tag or a regex object
                    # (a regex object will be formatted as the matched string by default)
                    data = {**tags, **matched}

                    def _tagwarn(msg, *args):
                        __log__.warning(
                            "Not setting tag '%s' on '%s' - " + msg,
                            k, tags["abspath"], *args
                        )
                    try:
                        v = format_template(v, data, tagmap=tagmap, fallbacks=fallbacks) or None
                    except KeyError as e:
                        _tagwarn("Failed to get tag '%s' for template '%s'", e.args[0], v)
                        continue
                    except RegexError as e:
                        _tagwarn("Failed to expand regex match for template '%s' (%s)", v, e)
                        continue
                    except FieldRegexExpandError as e:
                        _tagwarn("Tag '%s' is not a regex match in template '%s'", e.args[2], v)
                        continue

                if v is None:
                    # Pop tags that are an empty string or None out of the tags
                    if self.debug and k in tags:
                        __log__.info("Removed tag '%s'", k)
                    tags.pop(k, None)
                else:
                    if self.debug and (k not in tags or tags[k] != v):
                        __log__.info(
                            "Set tag '%s' to '%s' (was '%s')",
                            k, v, tags.get(k, "<unset>")
                        )
                    tags[k] = v

    def __repr__(self):
        return "<{} ({} -> {})>".format(
            self.__class__.__name__,
            " & ".join(
                ("{}~=/{}/".format(t, r.pattern) if isinstance(r, Pattern) else "{}={}".format(t, r))
                for t, r in self.rules.items()
            ),
            ",".join(
                "{}={}".format(*x)
                for x in self.operations.items()
            )
        )


def all_same(seq):
    """Check if everything in the sequence is the same"""
    i = iter(seq)
    try:
        f = next(i)
    except StopIteration:
        return True
    return all(f == x for x in i)


def try_keys(d, keys, default=None):
    """Try multiple keys in a dictionary until one is non-empty"""
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return default


def get_tag(tag, tags, *, tagmap, fallbacks=None, default=None):
    """Gets the value of a tag

    If the tag (or mapped tags) do not exist and no fallback is specified for
    the tag, None will be returned.

    If there is a matching fallback, it will be used. When processing string
    fallbacks, format_template is used. This allows for fallbacks like
    `"ALBUMARTIST": "{ARTIST}"` (use the artist if albumartist is blank)

    In general, do not provide fallbacks when analyzing the file since they add
    data that wasn't in the original tags. Fallbacks should be used in the
    output stage so the templates can be completed.

    Special case:
      - DISCNUMBER and TRACKNUMBERS will remove any total values ("1/5" --> "1")
    """
    keys = (tagmap or {}).get(tag) or [tag]
    data = try_keys(tags, keys)
    if not data and tag in (fallbacks or {}):
        # No key found - falling back to fallbacks
        fallback = fallbacks[tag]

        # If the fallback is a string, we need to treat it as template with variables
        if isinstance(fallback, str):
            try:
                data = format_template(fallback, tags, tagmap=tagmap, fallbacks=fallbacks)
            except KeyError as e:
                __log__.error(
                    "Unknown key '%s' in fallback '%s': '%s'",
                    e.args[0], tag, fallback
                )
                raise
        else:
            data = fallback

    # Special case - convert the format of "x/y" --> "x" for {DISC,TRACK}NUMBER
    if data and tag in {"DISCNUMBER", "TRACKNUMBER"} and isinstance(data, str):
        data = data.split("/")[0]

    if data is None:
        return default
    return data


def get_consistent_tag(tag, songs, *, tagmap, fallbacks=None, exclude_missing=False):
    """Get a tag only if the value of it is consistent across all songs

    See get_tag
    """
    vals = [
        get_tag(tag, s, tagmap=tagmap, fallbacks=fallbacks, default=None)
        for s in songs
    ]
    if exclude_missing:
        vals = [x for x in vals if x is not None]
    if vals and all_same(vals):
        return vals[0]
    return None


def format_template(template, data, *, tagmap, fallbacks=None):
    """Format a template using enhanced tag lookups

    Any variables are treated like tags and resolved using get_tag.
    """
    extra = {}
    for _, key, _, _ in REGEX_FORMATTER.parse(template):
        if not key:
            continue
        val = get_tag(key, data, tagmap=tagmap, fallbacks=fallbacks)
        if val is not None:
            extra[key] = val
    return REGEX_FORMATTER.format(template, **{**data, **extra})


def symlink_info(link_path):
    """Read path from a symlink, properly handling relative paths

    Returns the absolute path the symlink points at and if the symlink is relative
    """
    link_target = os.path.join(
        os.path.abspath(os.path.dirname(link_path)),
        os.readlink(link_path)
    )
    abs_path = os.path.normpath(link_target)
    return abs_path, (link_target != abs_path)


def in_directory(directory, path):
    """Check that the path is inside the directory"""
    # Note: Don't use Path.resolve since it follows symlinks
    path = Path(os.path.abspath(path))
    return directory != path and Path(os.path.commonpath((directory, path))) == directory


def process_linkdir(link_dir, music_dirs, *, existing=True, clean=True, relative_links=False):
    """Process data in the link directory

    If existing is True (default), will return a set of paths in any of the
    music_dirs that are symlinked from files in the link_dir

    If clean is True (default), will remove any broken links and empty
    directories
    """
    if not existing and not clean:
        return None

    __log__.info("Processing existing symlinks in '%s'", link_dir)

    exist = set()
    broken, empty = 0, 0
    for dirpath, dirs, files in os.walk(link_dir, topdown=False):
        for f in files:
            path = os.path.join(dirpath, f)
            if not os.path.islink(path):
                continue

            target, is_relative = symlink_info(path)
            if not os.path.exists(target):
                if clean:
                    # Broken symlink, remove
                    with contextlib.suppress(OSError):
                        os.remove(path)
                        broken += 1
                        __log__.debug("Deleted broken symlink: %s", path)
            elif (existing and
                  is_relative == relative_links and
                  any(in_directory(x, target) for x in music_dirs)
            ):
                # exists, is the proper link type, and links to within a music_dir
                exist.add(target)

        # Attempt to rmdir everything on the way up and catch the OSErrors
        if clean:
            with contextlib.suppress(OSError):
                os.rmdir(dirpath)
                empty += 1
                __log__.debug("Deleted empty directory: %s", dirpath)

    if clean:
        __log__.info(
            "Deleted %d broken symlinks and %d empty directories",
            broken, empty
        )
    if existing:
        __log__.info(
            "Found %d existing valid symlinks", len(exist)
        )
        return exist
    return None


def get_files(start):
    """Yield all the files under 'start' as (directory, filename) pairs"""
    if start.is_file():
        yield os.path.split(start.absolute())
    else:
        for dirpath, _, files in os.walk(start):
            for f in files:
                yield dirpath, f


def get_songs(music_dir, valid_files, *, overrides=None,
              tagmap, fallbacks=None, existing=None):
    """Scrape through the music directory and yield indvidual songs

    If existing is provided, all paths in it will be ignored
    """

    __log__.info("Scanning music files in '%s'", music_dir)
    existing = existing or set()
    file_regexes = [re.compile(x) for x in valid_files]
    success, linked, ignored, failed = 0, 0, 0, 0
    for dirpath, f in get_files(music_dir):
        if not any(x.fullmatch(f) for x in file_regexes):
            ignored += 1
            continue

        path = os.path.join(dirpath, f)
        if path in existing:
            linked += 1
            continue
        try:
            tags = taglib.File(path).tags
        except OSError:
            __log__.warning("Failed to parse tags from file: '%s'", path)
            failed += 1
            continue

        # Handle the case where the "music_dir" is a single file
        relpath = os.path.relpath(path, music_dir)
        if relpath == ".":
            relpath = f

        # Handle the case where the file has no extension
        ext = f.rsplit(".", 1)[-1]
        if ext == f:
            ext = None

        tags = {k: v[0].strip() or None for k, v in tags.items() if v}
        tags["abspath"] = path
        tags["path"] = relpath
        tags["ext"] = ext
        tags["filename"] = f
        __log__.debug("Scraped tags from file: '%s':\n%r", path, tags)
        success += 1

        if overrides:
            for o in overrides:
                o.apply(tags, tagmap=tagmap, fallbacks=fallbacks)

        yield tags

    __log__.info(
        "Found %d new songs (%d total files, %d already linked, %d non-music files ignored, %d failed)",
        success, success + ignored + linked + failed, linked, ignored, failed
    )


def album_id(tags, *, tagmap):
    """Make an album ID given the tags

    This ID will be the same for all songs on an album
    """
    # Lowercase to prevent capitalization from making different albums
    return tuple(
        get_tag(tn, tags, tagmap=tagmap, default="").lower()
        for tn in ALBUM_TAGS
    )


def group_by_album(songs, *, tagmap):
    """Group songs by albums and yield the groups"""
    albums = defaultdict(list)
    num = 0
    for num, song in enumerate(songs, 1):
        albums[album_id(song, tagmap=tagmap)].append(song)

    __log__.info("Grouped %d songs into %d albums", num, len(albums))

    for songs in albums.values():
        if not songs:
            continue
        yield songs


def get_links(albums, *, structure, tagmap, fallbacks):
    """Generates (dst link, src file) pairs for each song in each album"""

    charmap = str.maketrans(*structure["character_replace"], structure["character_strip"])

    for album in albums:
        # Use the album information from the first song's data. This prevents
        # issues where songs were grouped into the same album but the data was
        # cased differently (grouping is case-insensitive)
        album_tags = {
            tag: get_tag(tag, album[0], tagmap=tagmap)
            for tag in ALBUM_TAGS
        }

        # Figure out the format to use for the tracks
        multiartist = not all_same(
            get_tag("ARTIST", s, tagmap=tagmap, default="").lower()
            for s in album
        )
        if multiartist:
            album_track_fmt = structure["file_multiartist"]

            if bool(get_tag("ALBUMARTIST", album[0], tagmap=tagmap)):
                # Not a compilation if an albumartist is set (probably an anthology)
                album_fmt = structure["path"]
            elif not bool(get_tag("ALBUM", album[0], tagmap=tagmap)):
                # Not a compilation if the album is unknown (probably just missing tags)
                album_fmt = structure["path"]
            else:
                # No albumartist set - assume a compilation
                album_fmt = structure["path_compilation"]
        else:
            # All same artist -> not a compilation
            album_fmt = structure["path"]
            album_track_fmt = structure["file"]

        # If the is_compilation override is used it overrides all the above logic
        is_comp = get_consistent_tag("is_compilation", album, tagmap=tagmap)
        if is_comp is not None:
            album_fmt = structure["path_compilation"] if is_comp else structure["path"]

        # Check for multidisc
        multidisc = not all_same(get_tag("DISCNUMBER", s, tagmap=tagmap) for s in album)

        for song in album:
            if song.get("ignore"):
                # Still yield here so it can be counted in the stats
                yield (None, song["abspath"])
                continue

            # Use the album track formatting rules unless otherwise specified
            track_fmt = album_track_fmt

            # Override song tags with the album-level versions
            song.update(album_tags)

            if multidisc:
                track_fmt = structure["file_disc_prefix"] + track_fmt

            if song.get("preserve_filename"):
                track_fmt = "{filename}"

            path_template = song.get("path_template", "{}/{}".format(album_fmt, track_fmt))

            # Make sure the path_template is valid before attempting formatting
            # The following will raise an exception if it isn't
            all(REGEX_FORMATTER.parse(path_template))

            link_name = os.sep.join(
                format_template(
                    path_comp, song, tagmap=tagmap, fallbacks=fallbacks
                ).translate(charmap)
                for path_comp in RE_TEMPLATE_SLASHES.split(path_template)
            )
            yield (link_name, song["abspath"])


def make_links(link_dir, links, *, relative_links=False):
    """Make symlinks for each (name, source) pair in links

    Will make any required directories and only overwrite existing symlinks if required
    """
    existed, updated, ignored, created, failed = 0, 0, 0, 0, 0
    for link, source in links:
        if link is None:
            ignored += 1
            continue

        __log__.debug("%s ---> %s", link, source)

        link_path = link_dir.joinpath(link)
        if not in_directory(link_dir, link_path):
            __log__.warning(
                "Failed to symlink '%s' --> '%s': outside the link directory",
                link_path, source
            )
            failed += 1
            continue

        try:
            os.makedirs(link_path.parent, exist_ok=True)
        except OSError as e:
            __log__.warning(
                "Failed to make directory '%s': %s", link_path.parent, e
            )
            failed += 1
            continue

        is_update = False
        if os.path.exists(link_path):
            target, is_relative = symlink_info(link_path)
            if target == source and is_relative == relative_links:
                # exists and is already pointing at the correct file in the correct way
                existed += 1
                continue
            else:
                # incorrect link, remove it so it can be recreated
                is_update = True
                os.remove(link_path)
        try:
            if relative_links:
                os.symlink(
                    src=os.path.relpath(source, start=os.path.dirname(link_path)),
                    dst=link_path
                )
            else:
                os.symlink(src=source, dst=link_path)

            if is_update:
                updated += 1
            else:
                created += 1
        except OSError as e:
            __log__.warning(
                "Failed to symlink '%s' --> '%s': %s", link_path, source, e
            )
            failed += 1
            continue

    __log__.info(
        "Created %d new symlinks (%d updated, %d preexisting, %d ignored, %d failed)",
        created, updated, existed, ignored, failed
    )


def _make_symfarm(*, music_dirs, link_dir, clean: bool, rescan_existing: bool, relative_links: bool,
                  structure, valid_files, overrides, tagmap, fallbacks):
    """Main entry point - all options are required in full"""
    music_dirs = set(Path(x).resolve() for x in music_dirs)
    link_dir = Path(link_dir).resolve()

    # Convert the overrides to Override objects
    overrides = [Override(*x) for x in (overrides or [])]

    # Remove any music dirs that are subdirectores of other music dirs
    for md1, md2 in itertools.permutations(music_dirs, 2):
        if md2 in music_dirs and in_directory(md1, md2):
            __log__.debug(
                "Removing directory '%s' (will be scanned as part of '%s')",
                md2, md1
            )
            music_dirs.remove(md2)

    # Check the link directory isn't inside any music directories
    if any(x == link_dir or in_directory(x, link_dir) for x in music_dirs):
        raise ValueError(
            f"Link directory {link_dir} must not be a subdirectory of any music dirs"
        )

    existing = process_linkdir(
        link_dir, music_dirs, existing=not rescan_existing, clean=clean,
        relative_links=relative_links
    )

    songs = itertools.chain.from_iterable(
        get_songs(
            music_dir, valid_files, overrides=overrides, tagmap=tagmap,
            fallbacks=fallbacks, existing=existing
        ) for music_dir in music_dirs
    )
    albums = group_by_album(songs, tagmap=tagmap)
    links = get_links(albums, structure=structure, tagmap=tagmap, fallbacks=fallbacks)
    make_links(link_dir, links, relative_links=relative_links)
    __log__.info("Done!")


def make_symfarm(*, music_dirs, link_dir, **kwargs):
    """Make a symfarm

    Loads default values from the config file.
    """
    defaults = yaml.safe_load(pkg_resources.resource_stream(__name__, "defaults.yaml"))

    params = {
        "music_dirs": music_dirs,
        "link_dir": link_dir,
    }

    for k, v in (itertools.chain(defaults.pop("options").items(), defaults.items())):
        params[k] = kwargs[k] if k in kwargs else v

    _make_symfarm(**params)
