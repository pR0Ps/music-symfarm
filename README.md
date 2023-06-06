music-symfarm
=============

`music-symfarm` uses the tags stored in music files to create and manage a symlink farm pointing to
them. This allows perfectly tagged, but pathetically organized music collections to be browsed in
any file browser or to be read by applications that require music collections to be in a specific
format.

Details
-------
- The source music file/folder structure is not used in any way. A single folder of files will work
  in the same way that a highly nested collection would.
- The source music is assumed to be properly tagged in a way that the program can understand (see
  [TagLib](http://taglib.org/) for more info).
- Grouping tracks into albums is done by using the album name, year, and albumartist metadata within
  the individual tracks (case-insensitive comparisons are used).
- While the default options should be fine for most collections, pretty much everything (which
  filetypes to process, symlink structure, what text to use if data is missing, custom tag
  overrides, etc) can be configured.
- The included [defaults.yaml](./music_symfarm/defaults.yaml) config file contains the default
  configuration along with documentation on what each option does and how to change it.

Before/After Example
----------------------
Before - a gross mess of inconsistency (exaggerated for effect):
```bash
$ tree ~/Music/
"~/Music"
├── "_TODO - clean up this folder.txt"
├── "_From old hdd"
│   ├── "Awesome song by that band.mp3"
│   └── "An Artist - An Album (2004)"
│       ├── "01 - Track1.mp3"
│       ├── "02 - Track2.mp3"
│       └── "tracklist.txt"
├── "2005 - Some Hits [multidisc CD2]"
│   └── "01. The Bars - Bar.mp3"
├── "Some Hits [multidisc CD1]"
│   └── "Disc1 - 01 - Foo (The Foos).mp3"
```

Run the program:
```bash
$ music-symfarm ~/Music ~/symfarm/music
[INFO] Processing existing symlinks in '~/symfarm/music'
[INFO] Found 0 existing valid symlinks
[INFO] Scanning music files in '~/Music'
[INFO] Found 5 new songs (7 total files, 0 already linked, 2 ignored, 0 failed)
[INFO] Grouped 5 songs into 3 albums
[INFO] Created 5 new symlinks (0 updated, 0 preexisting, 0 failed)
[INFO] Done!
```

After - Organized music!
```bash
$ tree ~/symfarm/music
"~/symfarm/music"
├── "An Artist"
│   └── "An Album (2000)"
│       ├── "01 - First Track.mp3" -> "~/Music/_From old hdd/An Album (2004)/01 - Track1.mp3"
│       └── "02 - Second Track.mp3" -> "~/Music/_From old hdd/An Album (2004)/01 - Track2.mp3"
├── "That Band"
│   └── "Self-Titled (2010)"
│       └── "01 Awesome Song.mp3" -> "~/Music/_From old hdd/Awesome song by that band.mp3"
├── "Various Artists"
│   └── "Some Hits (2005)"
│       ├── "1-01 - The Foos - Foo.mp3" -> "~/Music/Some Hits [multidisc CD1]/Disc1 - 01 - Foo (The Foos).mp3"
│       └── "2-01 - The Bars - Bar.mp3" -> "~/Music/2005 - Some Hits [multidisc CD1]/01. The Bars - Bar.mp3"
```

Installation
------------
To set up a virtualenv and install `music-symfarm`:
```bash
$ python3 -m venv .venv
$ source .venv/bin/activate
(.venv)$ pip install git+https://github.com/pR0Ps/music-symfarm.git
(.venv)$ music-symfarm --help
```

Note that `pip` *should* automatically install the `pytaglib` dependency via a wheel that includes
the required `taglib` native library. If not, `taglib` and Python dev tools will need to be
installed first. See the https://github.com/supermihi/pytaglib project for more information.

Usage
-----
```bash
$ music-symfarm --help
usage: music-symfarm [-h] [--conf CONF] [--clean | --no-clean]
                     [--rescan-existing | --no-rescan-existing]
                     [--relative-links | --no-relative-links]
                     [--log {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
                     music_dir [music_dir ...] link_dir

Create a directory of symlinks based solely on music tags

positional arguments:
  music_dir             Music source directories
  link_dir              Directory where the symlinks will be created

options:
  -h, --help            show this help message and exit
  --conf CONF           A config file to override default settings
  --clean, --no-clean   Clean the link directory of broken links and empty
                        directories? (default: True)
  --rescan-existing, --no-rescan-existing
                        Rescan files that already have links pointing to them?
                        (default: False)
  --relative-links, --no-relative-links
                        Use relative paths for links instead of absolute?
                        (default: False)
  --log {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Set the logging level (default: INFO)
```

License
=======
Licensed under the [Mozilla Public License, version 2.0](https://www.mozilla.org/en-US/MPL/2.0)
