#!/usr/bin/env python

from collections import defaultdict
import contextlib
import logging
import os
from pathlib import Path
import re

import taglib


INVALID_CHAR_MAP = str.maketrans('<>:\/|"', "[]----'", "?*")
MUSIC_FILE_REGEX = re.compile(".+\.(flac|mp3|ogg|oga|wma)", re.IGNORECASE)
STRUCTURE = ["{ALBUMARTIST}", "{ALBUM} ({YEAR})"]
TRACK_FMT = "{TRACKNUMBER:02d} - {TITLE}.{ext}"
COMPILATION_TRACK_FMT = "{TRACKNUMBER:02d} - {ARTIST} - {TITLE}.{ext}"
DISCNUM_PREFIX_FMT = "{DISCNUMBER}-"
MULTIPLE_ARTISTS = "Various Artists"
UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
UNKNOWN_TITLE = "Unknown Title"
UNKNOWN_DATE = "0000-00-00"


__log__ = logging.getLogger(__name__)


def all_same(seq):
    """Check if everything in the sequence is the same"""
    i = iter(seq)
    try:
        f = next(i)
    except StopIteration:
        return True
    return all(f == x for x in i)


def try_keys(d, keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def get_title(tags, disp=False):
    return tags.get("TITLE", UNKNOWN_TITLE if disp else None)


def get_album(tags, disp=False):
    return tags.get("ALBUM", UNKNOWN_ALBUM if disp else None)


def get_artist(tags, disp=False):
    return tags.get("ARTIST", UNKNOWN_ARTIST if disp else None)


def get_albumartist(tags, disp=False):
    aa = tags.get("ALBUMARTIST", None)
    if disp and not aa:
        return get_artist(tags, disp=True)
    return aa


def get_date(tags, disp=False):
    date = try_keys(tags, ["ORIGINALDATE", "DATE", "YEAR"])
    if disp and not date:
        return UNKNOWN_DATE
    return date


def get_year(tags, disp=False):
    date = get_date(tags, disp=disp)
    if date:
        return date.split("-")[0]


def get_disc(tags, disp=False):
    num = try_keys(tags, ["DISCNUMBER", "DISKNUMBER", "DISC", "DISK"])
    with contextlib.suppress(TypeError, ValueError, AttributeError):
        return int(num.split("/")[0])
    return 1 if disp else None


def get_tracknumber(tags, disp=False):
    num = tags.get("TRACKNUMBER", None)
    with contextlib.suppress(TypeError, ValueError, AttributeError):
        return int(num.split("/")[0])
    return 0 if disp else None


def get_songs(music_dir):
    """Scrape through the music directory and yield indvidual songs"""
    success, ignored, failed = 0, 0, 0
    for dirpath, dirs, files in os.walk(music_dir):
        for f in files:
            if not MUSIC_FILE_REGEX.fullmatch(f):
                ignored += 1
                continue

            path = os.path.join(dirpath, f)
            try:
                tags = taglib.File(path).tags
            except OSError:
                __log__.warning("Failed to parse tags from file: '%s'", path)
                failed += 1
                continue
            tags = {k: v[0] for k, v in tags.items() if v}
            tags["path"] = path
            tags["ext"] = path.rsplit(".", 1)[-1]
            __log__.debug("Scraped tags from file: '%s'", path)
            success += 1
            yield tags

    __log__.info(
        "Found %d valid songs (%d total files, %d ignored, %d failed)",
        success, success + ignored + failed, ignored, failed
    )


def album_id(tags):
    """Make an album ID given the tags

    In other words, this has to be the same for all songs on an album
    """
    # Uses lowercase album name, date, and albumartist
    return tuple(map(
        lambda x: x.lower() if x else x,
        (get_album(tags), get_date(tags), get_albumartist(tags))
    ))


def group_by_album(songs):
    """Group songs by albums and yield the groups"""
    albums = defaultdict(list)
    num = 0
    for num, song in enumerate(songs):
        albums[album_id(song)].append(song)

    __log__.info("Grouped %d songs into %d albums", num + 1, len(albums))

    for songs in albums.values():
        if not songs:
            continue
        yield songs


ALBUM_MAPPING = {
        "ALBUMARTIST": get_albumartist,
        "ALBUM": get_album,
        "YEAR": get_year,
}
TRACK_MAPPING = {
        "ARTIST": get_artist,
        "DISCNUMBER": get_disc,
        "TRACKNUMBER": get_tracknumber,
        "TITLE": get_title
}


def get_links(albums):
    """Generates (dst link, src file) pairs for each song in each album"""

    for album in albums:
        # Use the album information from the first song's data. This prevents
        # issues where songs were grouped into the same album but the data was
        # slightly different (ex: casing)
        album_tags = {
            k: f(album[0], disp=True) for k, f in ALBUM_MAPPING.items()
        }

        # Figure out the format to use for the tracks

        # Check for multi-artist album (compilation)
        if all_same(get_artist(s) for s in album):
            track_fmt = TRACK_FMT
        else:
            album_tags["ALBUMARTIST"] = MULTIPLE_ARTISTS
            track_fmt = COMPILATION_TRACK_FMT

        # Check for multidisc
        if len(set(get_disc(s) for s in album)) > 1:
            track_fmt = DISCNUM_PREFIX_FMT + track_fmt

        for song in album:
            for key, fcn in TRACK_MAPPING.items():
                song[key] = fcn(song, disp=True)

            # Override song tags with the album-level version
            song.update(album_tags)

            link_name = os.sep.join(
                path_comp.format(**song).translate(INVALID_CHAR_MAP)
                for path_comp in STRUCTURE + [track_fmt]
            )
            yield (link_name, song["path"])


def make_links(link_dir, links):
    """Make symlinks for each (name, source) pair in links

    Will make any required directories and only overwrite existing symlinks if required
    """
    existed, updated, created, failed = 0, 0, 0, 0
    for link, source in links:
        __log__.debug("%s ---> %s", link, source)
        link_path = link_dir.joinpath(link)
        try:
            os.makedirs(link_path.parent, exist_ok=True)
        except OSError as e:
            __log__.warning(
                "Failed to make directory '%s': %s", link_path.parent, e
            )
            failed += 1
            continue
        if os.path.exists(link_path):
            if os.readlink(link_path) == source:
                # exists and is already pointing at the correct file
                existed += 1
                continue
            else:
                # incorrect link, remove it so it can be recreated
                updated += 1
                created -= 1
                os.remove(link_path)
        try:
            os.symlink(src=source, dst=link_path)
            created += 1
        except OSError as e:
            __log__.warning(
                "Failed to symlink '%s' --> '%s': %s", link_path, source, e
            )
            failed += 1
            continue

    __log__.info(
        "Created %d new symlinks (%d updated, %d preexisting, %d failed)",
        created, updated, existed, failed
    )


def clean_link_dir(link_dir):
    """Will remove any broken links and empty directories"""
    __log__.info("Cleaning link directory")

    broken, empty = 0, 0
    for dirpath, dirs, files in os.walk(link_dir, topdown=False, followlinks=False):
        for f in files:
            path = os.path.join(dirpath, f)
            if os.path.islink(path) and not os.path.exists(os.readlink(path)):
                # Broken symlink, remove
                with contextlib.suppress(OSError):
                    os.remove(path)
                    broken += 1
                    __log__.debug("Deleted broken symlink: %s", path)

        # Attempt to rmdir everything on the way up and catch the OSErrors
        with contextlib.suppress(OSError):
            os.rmdir(dirpath)
            empty += 1
            __log__.debug("Deleted empty directory: %s", dirpath)

    __log__.info(
        "Deleted %d broken symlinks and %d empty directories",
        broken, empty
    )


def make_symfarm(*, music_dir, link_dir, clean=False):
    """Main entry point"""
    music_dir = Path(music_dir)
    link_dir = Path(link_dir)

    if Path(os.path.commonpath((music_dir, link_dir))) == music_dir:
        raise ValueError("Link directory must not be inside the music directory")

    if clean:
        clean_link_dir(link_dir)

    __log__.info("Scanning music files in '%s'", music_dir)
    songs = get_songs(music_dir)
    albums = group_by_album(songs)
    links = get_links(albums)
    make_links(link_dir, links)
    __log__.info("Done!")
