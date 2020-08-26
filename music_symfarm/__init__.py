#!/usr/bin/env python

from collections import defaultdict
import contextlib
import itertools
import logging
import os
from pathlib import Path
import re
from string import Formatter
from typing import Pattern

import pkg_resources
import taglib
import yaml


# Tags that have to be the same for songs to be considered in the same album
ALBUM_TAGS = ("ALBUMARTIST", "ALBUM", "DATE")


__log__ = logging.getLogger(__name__)


class RegexFormatter(Formatter):
    """A string formatter that does regex substitutions

    Ex: {0:/pattern/repl/<other formatting options>}
    """
    def format_field(self, value, format_spec):
        if format_spec.startswith("/"):
            _, pattern, repl, format_spec = format_spec.split("/", 3)
            value = re.sub(pattern, repl, value)
        return super().format_field(value, format_spec)

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
        if isinstance(rule, Pattern) and data is not None:
            return rule.fullmatch(data)
        return rule == data

    def matches(self, tags, *, tagmap):
        """Check if this override should be applied, given the provided tags"""
        for tag_name, rule in self.rules.items():
            data = get_tag(tag_name, tags, tagmap=tagmap)
            if not self._rule_match(rule, data):
                return False
        return True

    def apply(self, tags, *, tagmap, fallbacks):
        """Apply the overrides to the tags (modifies tags in-place)"""
        if self.matches(tags, tagmap=tagmap):
            if self.debug:
                __log__.info("Song '%s' matched override %r ", tags["abspath"], self)
            for k, v in self.operations.items():
                # Apply any formatting if the target is a string
                # (treat empty string as None)
                if isinstance(v, str):
                    v = format_template(v, tags, tagmap=tagmap, fallbacks=fallbacks) or None

                if v is None:
                    # Pop tags that are an empty string or None out of the tags
                    if self.debug:
                        __log__.info("Removed '%s'", k)
                    tags.pop(k, None)
                else:
                    if self.debug:
                        __log__.info("Set '%s' to '%s' (was '%s')", k, v, tags.get(k, "<unset>"))
                    tags[k] = v

    def __repr__(self):
        return "<Override ({} -> {})>".format(
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

    # Special case:
    if data and tag in {"DISCNUMBER", "TRACKNUMBER"}:
        with contextlib.suppress(TypeError, ValueError, AttributeError):
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


def get_songs(music_dir, valid_files, *, overrides=None,
              tagmap, fallbacks=None, existing=None):
    """Scrape through the music directory and yield indvidual songs

    If existing is provided, all paths in it will be ignored
    """

    __log__.info("Scanning music files in '%s'", music_dir)
    existing = existing or set()
    file_regexes = [re.compile(x) for x in valid_files]
    success, linked, ignored, failed = 0, 0, 0, 0
    for dirpath, dirs, files in os.walk(music_dir):
        for f in files:
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
            tags = {k: v[0].strip() for k, v in tags.items() if v}
            tags["abspath"] = path
            tags["path"] = os.path.relpath(path, music_dir)
            tags["ext"] = path.rsplit(".", 1)[-1]
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
            tag: get_tag(tag, album[0], tagmap=tagmap, fallbacks=fallbacks)
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

            path_format = song.get("path_format", "{}/{}".format(album_fmt, track_fmt))

            link_name = os.sep.join(
                format_template(
                    path_comp, song, tagmap=tagmap, fallbacks=fallbacks
                ).translate(charmap)
                for path_comp in path_format.split("/")
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
