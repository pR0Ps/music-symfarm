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
STRUCTURE = ["{ALBUMARTIST}", "{ALBUM} ({DATE})"]
TRACK_FMT = "{TRACKNUMBER:02d} - {TITLE}.{ext}"
COMPILATION_TRACK_FMT = "{TRACKNUMBER:02d} - {ARTIST} - {TITLE}.{ext}"
DISCNUM_PREFIX_FMT = "{DISCNUMBER}-"


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


def get_album(tags, disp=False):
    return tags.get("ALBUM", "Unknown Album" if disp else None)


def get_artist(tags, disp=False):
    return tags.get("ARTIST", "Unknown Artist" if disp else None)


def get_albumartist(tags, disp=False):
    aa = tags.get("ALBUMARTIST", None)
    if disp and not aa:
        return get_artist(tags, True)
    return aa


def get_date(tags, disp=False):
    date = try_keys(tags, ["DATE", "ORIGINALDATE", "YEAR"])
    if date is None:
        return "0000" if disp else None
    return date.split("-")[0]


def get_disc(tags, disp=False):
    num = try_keys(tags, ["DISCNUMBER", "DISKNUMBER", "DISC", "DISK"])
    with contextlib.suppress(TypeError, AttributeError):
        return int(num.split("/")[0])
    return 1 if disp else None


def get_tracknumber(tags, disp=False):
    num = tags.get("TRACKNUMBER", None)
    with contextlib.suppress(TypeError, AttributeError):
        return int(num.split("/")[0])
    return 0 if disp else None


def get_title(tags, disp=False):
    return tags.get("TITLE", None)


MAPPING = {
        "ALBUMARTIST": get_albumartist,
        "ARTIST": get_artist,
        "ALBUM": get_album,
        "DATE": get_date,
        "DISCNUMBER": get_disc,
        "TRACKNUMBER": get_tracknumber,
        "TITLE": get_title
}


def get_links(music_dir):
    """Scrape through the directory and yield (dst link, src file) pairs"""

    # Group all files by album (an album is the base "unit" of music)
    __log__.info("Scraping information from music files")
    albums = defaultdict(list)
    for dirpath, dirs, files in os.walk(music_dir):
        for f in files:
            if not MUSIC_FILE_REGEX.match(f):
                continue

            path = os.path.join(dirpath, f)
            tags = taglib.File(path).tags
            tags = {k: v[0] for k, v in tags.items() if v}
            tags["path"] = path
            tags["ext"] = path.rsplit(".", 1)[-1]
            albums[get_album(tags)].append(tags)

    __log__.info("Attempting to deduplicate album names using the albumartist tag")
    # Check for album name collisions (the "Greatest Hits" problem)
    # Only works if the ALBUMARTIST tag is actually set in the media
    newalbums = defaultdict(list)
    for album, songs in albums.items():
        album_artists = defaultdict(list)
        for song in songs:
            album_artists[get_albumartist(song)].append(song)
        for aa, songs in album_artists.items():
            # This key is only used internally (not output anywhere)
            newalbums[album + str(aa)] = songs
    albums = newalbums

    __log__.info("Generating link paths from the music metadata")
    for songs in albums.values():
        if not songs:
            continue

        album = get_album(songs[0])
        compilation = not all_same(get_artist(x) for x in songs)
        num_discs = len(set(get_disc(x) for x in songs))

        for s in songs:
            for key, fcn in MAPPING.items():
                s[key] = fcn(s, disp=True)

            track_fmt = TRACK_FMT

            if compilation:
                s["ALBUMARTIST"] = "Various Artists"
                track_fmt = COMPILATION_TRACK_FMT

            if num_discs > 1:
                track_fmt = DISCNUM_PREFIX_FMT + track_fmt

            link_name = os.sep.join(x.format(**s).translate(INVALID_CHAR_MAP) for x in STRUCTURE + [track_fmt])
            yield (link_name, s["path"])


def make_links(link_dir, links):
    """Make symlinks for each (name, source) pair in links

    Will make any required directories and only overwrite existing symlinks if required
    """
    __log__.info("Creating symlinks")
    for link, source in links:
        __log__.debug("%s ---> %s", link, source)
        link_path = link_dir.joinpath(link)
        os.makedirs(link_path.parent, exist_ok=True)
        if os.path.exists(link_path):
            if os.readlink(link_path) == source:
                # exists and is already pointing at the correct file
                continue
            else:
                # incorrect link, remove it
                os.remove(link_path)
        os.symlink(src=source, dst=link_path)


def clean_link_dir(link_dir):
    """Will remove any broken links and empty directories"""
    __log__.info("Removing broken symlinks and empty directories from the link dir")

    for dirpath, dirs, files in os.walk(link_dir, topdown=False, followlinks=False):
        num_files = len(files)
        for f in files:
            path = os.path.join(dirpath, f)
            if os.path.islink(path) and not os.path.exists(os.readlink(path)):
                # Broken symlink, remove
                __log__.debug("Deleting broken symlink: %s", path)
                os.remove(path)
                num_files -= 1

        if not num_files:
            # Refresh the dir list since we may have already deleted things at a lower level
            if not any(os.path.exists(os.path.join(dirpath, x)) for x in dirs):
                __log__.debug("Deleting empty directory: %s", dirpath)
                os.rmdir(dirpath)


def make_symfarm(*, music_dir, link_dir, clean=False):
    """Main entry point"""
    music_dir = Path(music_dir)
    link_dir = Path(link_dir)

    if Path(os.path.commonpath((music_dir, link_dir))) == music_dir:
        raise ValueError("Link directory must not be inside the music directory")

    if clean:
        clean_link_dir(link_dir)

    links = get_links(music_dir)
    make_links(link_dir, links)
    __log__.info("Done!")
