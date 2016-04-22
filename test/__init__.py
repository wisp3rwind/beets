# -*- coding: utf-8 -*-
# This file is part of beets.
# Copyright 2016, Adrian Sampson.
# Copyright 2016, Thomas Scholtes.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

# TODO:
# ensure everything gets cleaned up automatically
# purge redundant temp_dir code from tests
# check IO-mocking code in tests


"""This module includes various helpers that provide fixtures, capture
information or mock the environment.

- The `control_stdin` and `capture_stdout` context managers allow one to
  interact with the user interface.

- `has_program` checks the presence of a command on the system.

- The `generate_album_info` and `generate_track_info` functions return
  fixtures to be used when mocking the autotagger.

- The `TestImportSession` allows one to run importer code while
  controlling the interactions through code.

- The `TestHelper` class encapsulates various fixtures that can be set up.
"""

# TODO Move AutotagMock here

from __future__ import division, absolute_import, print_function

import sys
import os
import os.path
import shutil
import time
import subprocess
from tempfile import mkdtemp, mkstemp
from contextlib import contextmanager
from functools import wraps
from StringIO import StringIO
from enum import Enum

# Use unittest2 on Python < 2.7.
try:
    import unittest2 as unittest
except ImportError:
    import unittest

# Mangle the search path to include the beets sources.
sys.path.insert(0, '..')  # noqa
import beets
import beets.config
import beets.library
import beets.plugins
from beets import importer, logging
# from beets.ui import commands
from beets.library import Library, Item, Album
from beets.autotag.hooks import AlbumInfo, TrackInfo
from beets.mediafile import MediaFile, Image
from beets.ui import _arg_encoding

# Make sure the development versions of the plugins are used
import beetsplug
beetsplug.__path__ = [os.path.abspath(
    os.path.join(__file__, '..', '..', 'beetsplug')
)]


# OS feature test.
HAVE_SYMLINK = hasattr(os, 'symlink')

# Test resources path.
RSRC = os.path.join(os.path.dirname(__file__), b'rsrc')


# Propagate to root loger so nosetest can capture it
log = logging.getLogger('beets')
log.propagate = True
log.setLevel(logging.DEBUG)


# Mock timing.


@contextmanager
def Timecop():
    """Mocks the timing system (namely time() and sleep()) for testing.
    Inspired by the Ruby timecop library.

    >>> with Timecop():
    >>>     time.sleep(1)
    """
    now = time.time()
    *orig = time.time, time.sleep

    def time():
        return now

    def sleep(amount):
        now += amount

    time.time, time.sleep = time, sleep
    yield
    time.time, time.sleep = *orig


# Mock IO


class InputException(Exception):
    def __init__(self, output=None):
        self.output = output

    def __str__(self):
        msg = "Attempt to read with no input provided."
        if self.output is not None:
            msg += " Output: {!r}".format(self.output)
        return msg


class control_stdin():
    """Sends ``input`` to stdin.

    >>> with control_stdin('yes') as inp:
    ...     input()
    ...     inp.addlines('no', 'foo')
    ...     input()
    ...     input()
    'yes'
    'no'
    'foo'
    >>> inp.readcount()
    2

    Will raise ``InputException`` on readline calls when the buffer is
    exhausted.
    The error message in that case can be enhanced if stdout is captured and
    a related prompt is expected:

    >>> with control_stdout() as out, control_stdin(out=out) as inp:
    ...    input("Enter the name:")
    InputException: "Enter the name:"
    """

    def __enter__(self, input=u'', out=None):
        self.org = sys.stdin
        sys.stdin = StringIO(input)
        sys.stdin.encoding = 'utf8'
        self.readcount = 0
        self.out = out
        return self

    def __exit__(self):
        sys.stdin = self.org

    # Untested, not in use in any test right now. Here to keep the
    # functionality DummyIn had.
    def addline(self, *input):
        pos = sys.stdin.tell()
        sys.stdin.seek(0, os.SEEK_END)
        sys.stdin.write(u'\n'.join(input) + u'\n')
        sys.stdin.seek(pos)

    # Same here. Functionality from DummyIn, might be unused
    def readline(self, *args, **kwargs):
        self.readcount += 1
        res = super(control_stdin, self).readline(*args, **kwargs)
        if not res:
            if self.out:
                raise InputException(self.out.getvalue())
            else:
                raise InputException()
        return res


@contextmanager
def capture_stdout():
    """Save stdout in a StringIO.

    >>> with capture_stdout() as output:
    ...     print('spam')
    ...
    >>> output.getvalue()
    'spam'
    """
    org = sys.stdout
    sys.stdout = capture = StringIO()
    sys.stdout.encoding = 'utf8'
    try:
        yield sys.stdout
    finally:
        sys.stdout = org
        print(capture.getvalue())


class _LogCapture(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.messages = []

    def emit(self, record):
        self.messages.append(unicode(record.msg))


@contextmanager
def capture_log(logger='beets'):
    """Capture logs emitted through ``logging``, by default listens on the
    main ``beets```logger, i.e. everything. Yields a list of the logged
    messages.

    >>> with capture_log as msg:
    ...    log.debug('stuff')
    >>> msg
    ['stuff']
    """
    capture = _LogCapture()
    log = logging.getLogger(logger)
    log.addHandler(capture)
    try:
        yield capture.messages
    finally:
        log.removeHandler(capture)


# Platform mocking.


@contextmanager
def platform_windows():
    """Load ntpath as os.path
    """
    import ntpath
    old_path = os.path
    try:
        os.path = ntpath
        yield
    finally:
        os.path = old_path


@contextmanager
def platform_posix():
    """Load posixpath as os.path
    """
    import posixpath
    old_path = os.path
    try:
        os.path = posixpath
        yield
    finally:
        os.path = old_path


@contextmanager
def system_mock(name):
    """Mocks the system name.

    >>> import platform
    >>> platform.system()
    'Linux'
    >>> with system_mock('Windows'):
    ...     platform.system()
    'Windows'
    """
    import platform
    old_system = platform.system
    platform.system = lambda: name
    try:
        yield
    finally:
        platform.system = old_system


# Utility.


# TODO: replace by collections.defaultdict ?
class Bag(object):
    """An object that exposes a set of fields given as keyword
    arguments. Any field not found in the dictionary appears to be None.
    Used for mocking Album objects and the like.
    """
    def __init__(self, **fields):
        self.fields = fields

    def __getattr__(self, key):
        return self.fields.get(key)


def has_program(cmd, args=['--version']):
    """Returns `True` if `cmd` can be executed.
    """
    full_cmd = [cmd] + args
    for i, elem in enumerate(full_cmd):
        if isinstance(elem, unicode):
            full_cmd[i] = elem.encode(_arg_encoding())
    try:
        with open(os.devnull, 'wb') as devnull:
            subprocess.check_call(full_cmd, stderr=devnull,
                                  stdout=devnull, stdin=devnull)
    except OSError:
        return False
    except subprocess.CalledProcessError:
        return False
    else:
        return True


# Extend TestCase


class TestCase(unittest.TestCase):
    """A unittest.TestCase subclass that saves and restores beets'
    global configuration. This allows tests to make temporary
    modifications that will then be automatically removed when the test
    completes. Also provides some additional assertion methods and
    temporary directory.
    """

    # Clean coonfiguration for every test

    def setUp(self):
        """Setup pristine global configuration and library for testing.

        Sets ``beets.config`` so we can safely use any functionality
        that uses the global configuration.  All paths used are
        contained in a temporary directory

        Sets the following properties on itself.

        - ``temp_dir`` Path to a temporary directory containing all
          files specific to beets

        - ``libdir`` Path to a subfolder of ``temp_dir``, containing the
          library's media files. Same as ``config['directory']``.

        - ``config`` The global configuration used by beets.

        - ``lib`` Library instance created with the settings from
          ``config``.
        """
        # A "clean" source list including only the defaults.
        beets.config.clear()
        beets.config.read(user=False, defaults=True)

        # Direct paths to a temporary directory. Tests can also use this
        # temporary directory.
        # TODO: how much time does this consume? should it be done more lazily?
        # How many tests actually need a temporary dir?
        self.temp_dir = mkdtemp()
        beets.config['statefile'] = os.path.join(self.temp_dir, 'state.pickle')
        beets.config['library'] = os.path.join(self.temp_dir, 'library.db')
        self.libdir = os.path.join(self.temp_dir, 'libdir')
        beets.config['directory'] = self.libdir

        self.config['plugins'] = []
        self.config['verbose'] = 1
        # self.config['ui']['color'] = False
        # self.config['threaded'] = False

        # load test-specific config supplied by the wrapper if it is supposed
        # to run here. This is useful have different configurations per
        # testcase, but still load plugins in setUp()
        func = self.getattr(self._testMethodName)
        if hasattr(func, '__beets_config') and \
                func.__beets_config_before_setup:
            # use set(), not add(). This way, it is the highest priority source
            # until more options are set()
            beets.config.set(func.__beets_config)

        # Set $HOME, which is used by confit's `config_dir()` to create
        # directories.
        os.environ['BEETSDIR'] = self.temp_dir
        self._old_home = os.environ.get('HOME')
        os.environ['HOME'] = self.temp_dir

        self._loaded_plugins = []

    def tearDown(self):
        if self._loaded_plugins:
            # Unload all plugins and remove the from the configuration.
            # FIXME this should eventually be handled by a plugin manager
            beets.plugins._classes = set()
            beets.plugins._instances = {}
            Item._types = Item._original_types
            Album._types = Album._original_types

        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir)

        if 'BEETSDIR' in os.environ:
            del os.environ['BEETSDIR']
        if self._old_home is None:
            del os.environ['HOME']
        else:
            os.environ['HOME'] = self._old_home
        self.io.restore()

        beets.config.clear()
        beets.config._materialized = False

    def with_config(self, func, config, before_setup=True):
        """A decorator to simplify configuration changes per test method.
        When setting `before_setup`, the changes will be applied in this
        class' setUp(). This way, the configuration already is in effect in
        the test module's setUp() and can, for example, influence plugins
        loaded there.

        >>> class UseThePlugin(TestCase):
        ...     def setUp(self):
        ...         super(UseThePlugin, self).setUp()
        ...         # load plugin with per-method config
        ...         self.plug = theplugin.ThePlugin()
        ...
        ... class MyTest(UseThePlugin):
        ...     @with_config({u'theplugin': {u'the_answer': 42}})
        ...     def test_it(self):
        ...         # when the plugin was loaded, the configuration set
        ...         # through the decorator was in effect!
        ...         pass

        Order of precedence when setting options with this decorator (if
        nothing else is stated 'set' means set via `config.set()` or
        `config['opt'] = `):
            - options set in the test method will shadow everything else
            - options set through the decorator with `before_setup=False` will
              shadow options set in setUp()
            - options set in setUp() will shadow those set through the
              decorator with `before_setup=True`
            - anything set per config.add() will be shadowed by all of the
              above methods
        """
        func.__beets_config = config
        func.__beets_config_before_setup = before_setup

        @wraps(func)
        def apply_config(*args, **kwargs):
            if not func.__beets_config_before_setup:
                beets.config.set(config)
            func(*args, **kwargs)

        return apply_config

    # TODO: make this usable both from setUp() and as a contextmanager
    def load_plugins(self, *plugins):
        """Load and initialize plugins by names.
        Similar to setting a list of plugins in the configuration. Will
        be unloaded by tearDown()
        """
        self._loaded_plugins.extend(plugins)
        # FIXME this should eventually be handled by a plugin manager
        beets.config['plugins'].extend(plugins)
        beets.plugins.load_plugins(plugins)
        beets.plugins.find_plugins()
        # Take a backup of the original _types to restore when unloading
        if not hasattr(Item, '_original_types'):
            Item._original_types = dict(Item._types)
        if not hasattr(Album, '_original_types'):
            Album._original_types = dict(Album._types)
        Item._types.update(beets.plugins.types(Item))
        Album._types.update(beets.plugins.types(Album))

    def slow_test(unused=None):
        def _id(obj):
            return obj
        if 'SKIP_SLOW_TESTS' in os.environ:
            return unittest.skip(u'test is slow')
        return _id

    # convenient assertions

    def assertExists(self, path):
        self.assertTrue(os.path.exists(path),
                        u'file does not exist: {!r}'.format(path))

    def assertNotExists(self, path):
        self.assertFalse(os.path.exists(path),
                         u'file exists: {!r}'.format((path)))

    # Safe file operations

    def touch(self, path, dir=None, content=''):
        """Create a file at `path` with given content.

        If `dir` is given, it is prepended to `path`. After that, if the
        path is relative, it is resolved with respect to
        `self._temp_dir`.
        """
        if dir:
            path = os.path.join(dir, path)

        if not os.path.isabs(path):
            path = os.path.join(self.temp_dir, path)

        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)

        with open(path, 'a+') as f:
            f.write(content)
        return path

    # dummy items/albums

    _item_ident = 0

    def create_item(self):
        self._item_ident += 1
        i = beets.library.Item(
            title=u'the title',
            artist=u'the artist',
            albumartist=u'the album artist',
            album=u'the album',
            genre=u'the genre',
            composer=u'the composer',
            grouping=u'the grouping',
            year=1,
            month=2,
            day=3,
            track=4,
            tracktotal=5,
            disc=6,
            disctotal=7,
            lyrics=u'the lyrics',
            comments=u'the comments',
            bpm=8,
            comp=True,
            path='somepath{0}'.format(self._item_ident),
            length=60.0,
            bitrate=128000,
            format='FLAC',
            mb_trackid='someID-1',
            mb_albumid='someID-2',
            mb_artistid='someID-3',
            mb_albumartistid='someID-4',
            album_id=None,
        )
        if hasattr(self, 'lib'):
            self.lib.add(i)
        return i

    _album_ident = 0

    def create_album(self):
        self._item_ident += 1
        i = beets.library.Album(
            artpath=None,
            albumartist=u'some album artist',
            albumartist_sort=u'some sort album artist',
            albumartist_credit=u'some album artist credit',
            album=u'the album',
            genre=u'the genre',
            year=2014,
            month=2,
            day=5,
            tracktotal=0,
            disctotal=1,
            comp=False,
            mb_albumid='someID-1',
            mb_albumartistid='someID-1'
        )
        if hasattr(self, 'lib'):
            self.lib.add(i)
        return i

    ALBUM_INFO_FIELDS = ['album', 'album_id', 'artist', 'artist_id',
                         'asin', 'albumtype', 'va', 'label',
                         'artist_sort', 'releasegroup_id', 'catalognum',
                         'language', 'country', 'albumstatus', 'media',
                         'albumdisambig', 'artist_credit',
                         'data_source', 'data_url']

    def generate_album_info(self, album_id, track_ids):
        """Return `AlbumInfo` populated with mock data.

        Sets the album info's `album_id` field is set to the corresponding
        argument. For each value in `track_ids` the `TrackInfo` from
        `generate_track_info` is added to the album info's `tracks` field.
        Most other fields of the album and track info are set to "album
        info" and "track info", respectively.
        """
        tracks = [self.generate_track_info(id) for id in track_ids]
        album = AlbumInfo(
            album_id=u'album info',
            album=u'album info',
            artist=u'album info',
            artist_id=u'album info',
            tracks=tracks,
        )
        for field in self.ALBUM_INFO_FIELDS:
            setattr(album, field, u'album info')

        return album

    TRACK_INFO_FIELDS = ['artist', 'artist_id', 'artist_sort',
                         'disctitle', 'artist_credit', 'data_source',
                         'data_url']

    def generate_track_info(self, track_id='track info', values={}):
        """Return `TrackInfo` populated with mock data.

        The `track_id` field is set to the corresponding argument. All other
        string fields are set to "track info".
        """
        track = TrackInfo(
            title=u'track info',
            track_id=track_id,
        )
        for field in self.TRACK_INFO_FIELDS:
            setattr(track, field, u'track info')
        for field, value in values.items():
            setattr(track, field, value)
        return track

    # Library fixtures methods
    # TODO: should some of these go to LibTestCase

    def create_item(self, **values):
        """Return an `Item` instance with sensible default values.

        The item receives its attributes from `**values` paratmeter. The
        `title`, `artist`, `album`, `track`, `format` and `path`
        attributes have defaults if they are not given as parameters.
        The `title` attribute is formated with a running item count to
        prevent duplicates. The default for the `path` attribute
        respects the `format` value.

        The item is attached to the database from `self.lib`.
        """
        item_count = self._get_item_count()
        values_ = {
            'title': u't\u00eftle {0}',
            'artist': u'the \u00e4rtist',
            'album': u'the \u00e4lbum',
            'track': item_count,
            'format': 'MP3',
        }
        values_.update(values)
        values_['title'] = values_['title'].format(item_count)
        values_['db'] = self.lib
        item = Item(**values_)
        if 'path' not in values:
            item['path'] = 'audio.' + item['format'].lower()
        return item

    def add_item(self, **values):
        """Add an item to the library and return it.

        Creates the item by passing the parameters to `create_item()`.

        If `path` is not set in `values` it is set to `item.destination()`.
        """
        item = self.create_item(**values)
        item.add(self.lib)
        if 'path' not in values:
            item['path'] = item.destination()
            item.store()
        return item

    def add_item_fixture(self, **values):
        """Add an item with an actual audio file to the library.
        """
        item = self.create_item(**values)
        extension = item['format'].lower()
        item['path'] = os.path.join(RSRC, 'min.' + extension)
        item.add(self.lib)
        item.move(copy=True)
        item.store()
        return item

    def add_album(self, **values):
        item = self.add_item(**values)
        return self.lib.add_album([item])

    def add_item_fixtures(self, ext='mp3', count=1):
        """Add a number of items with files to the database.
        """
        # TODO base this on `add_item()`
        items = []
        path = os.path.join(RSRC, 'full.' + ext)
        for i in range(count):
            item = Item.from_path(bytes(path))
            item.album = u'\u00e4lbum {0}'.format(i)  # Check unicode paths
            item.title = u't\u00eftle {0}'.format(i)
            item.add(self.lib)
            item.move(copy=True)
            item.store()
            items.append(item)
        return items

    def add_album_fixture(self, track_count=1, ext='mp3'):
        """Add an album with files to the database.
        """
        items = []
        path = os.path.join(RSRC, 'full.' + ext)
        for i in range(track_count):
            item = Item.from_path(bytes(path))
            item.album = u'\u00e4lbum'  # Check unicode paths
            item.title = u't\u00eftle {0}'.format(i)
            item.add(self.lib)
            item.move(copy=True)
            item.store()
            items.append(item)
        return self.lib.add_album(items)

    def create_mediafile_fixture(self, ext='mp3', images=[]):
        """Copies a fixture mediafile with the extension to a temporary
        location and returns the path.

        It keeps track of the created locations and will delete the with
        `remove_mediafile_fixtures()`

        `images` is a subset of 'png', 'jpg', and 'tiff'. For each
        specified extension a cover art image is added to the media
        file.
        """
        src = os.path.join(RSRC, 'full.' + ext)
        handle, path = mkstemp()
        os.close(handle)
        shutil.copyfile(src, path)

        if images:
            mediafile = MediaFile(path)
            imgs = []
            for img_ext in images:
                img_path = os.path.join(RSRC,
                                        'image-2x3.{0}'.format(img_ext))
                with open(img_path, 'rb') as f:
                    imgs.append(Image(f.read()))
            mediafile.images = imgs
            mediafile.save()

        if not hasattr(self, '_mediafile_fixtures'):
            self._mediafile_fixtures = []
        self._mediafile_fixtures.append(path)

        return path

    def remove_mediafile_fixtures(self):
        if hasattr(self, '_mediafile_fixtures'):
            for path in self._mediafile_fixtures:
                os.remove(path)

    def _get_item_count(self):
        if not hasattr(self, '__item_count'):
            count = 0
        self.__item_count = count + 1
        return count

    # Running beets commands
    # TODO: do tests using this always need a library? Then move
    # to LibTestCase

    def run_command(self, *args):
        if hasattr(self, 'lib'):
            lib = self.lib
        else:
            lib = Library(':memory:')
        beets.ui._raw_main(list(args), lib)

    def run_with_output(self, *args):
        with capture_stdout() as out:
            self.run_command(*args)
        return out.getvalue().decode('utf-8')


class LibTestCase(TestCase):
    """A test case that includes an in-memory library object (`lib`) and
    an item added to the library (`i`).
    # TODO: check whether i is actually used anywhere
    """
    def setUp(self, disk=False, **kwargs):
        super(LibTestCase, self).setUp(**kwargs)
        if disk:
            os.mkdir(self.libdir)
            dbpath = self.config['library'].as_filename()
        else:
            dbpath = ':memory:'
        self.lib = Library(dbpath, self.libdir)
        self.i = self.create_item()

    def tearDown(self):
        del self.lib._connections
        self.lib._connection().close()
        super(LibTestCase, self).tearDown()

    def create_importer(self, item_count=1, album_count=1):
        """Create files to import and return corresponding session.

        Copies the specified number of files to a subdirectory of
        `self.temp_dir` and creates a `TestImportSession` for this path.
        """
        import_dir = os.path.join(self.temp_dir, 'import')
        if not os.path.isdir(import_dir):
            os.mkdir(import_dir)

        album_no = 0
        while album_count:
            album = u'album {0}'.format(album_no)
            album_dir = os.path.join(import_dir, album)
            if os.path.exists(album_dir):
                album_no += 1
                continue
            os.mkdir(album_dir)
            album_count -= 1

            track_no = 0
            album_item_count = item_count
            while album_item_count:
                title = u'track {0}'.format(track_no)
                src = os.path.join(RSRC, 'full.mp3')
                dest = os.path.join(album_dir, '{0}.mp3'.format(title))
                if os.path.exists(dest):
                    track_no += 1
                    continue
                album_item_count -= 1
                shutil.copy(src, dest)
                mediafile = MediaFile(dest)
                mediafile.update({
                    'artist': 'artist',
                    'albumartist': 'album artist',
                    'title': title,
                    'album': album,
                    'mb_albumid': None,
                    'mb_trackid': None,
                })
                mediafile.save()

        self.config['import']['quiet'] = True
        self.config['import']['autotag'] = False
        self.config['import']['resume'] = False

        return TestImportSession(self.lib, loghandler=None, query=None,
                                 paths=[import_dir])


class TestImportSession(importer.ImportSession):
    """ImportSession that can be controlled programaticaly.

    >>> lib = Library(':memory:')
    >>> importer = TestImportSession(lib, paths=['/path/to/import'])
    >>> importer.add_choice(importer.action.SKIP)
    >>> importer.add_choice(importer.action.ASIS)
    >>> importer.default_choice = importer.action.APPLY
    >>> importer.run()

    This imports ``/path/to/import`` into `lib`. It skips the first
    album and imports thesecond one with metadata from the tags. For the
    remaining albums, the metadata from the autotagger will be applied.
    """

    def __init__(self, *args, **kwargs):
        super(TestImportSession, self).__init__(*args, **kwargs)
        self._choices = []
        self._resolutions = []

    default_choice = importer.action.APPLY

    def add_choice(self, choice):
        self._choices.append(choice)

    def clear_choices(self):
        self._choices = []

    def choose_match(self, task):
        try:
            choice = self._choices.pop(0)
        except IndexError:
            choice = self.default_choice

        if choice == importer.action.APPLY:
            return task.candidates[0]
        elif isinstance(choice, int):
            return task.candidates[choice - 1]
        else:
            return choice

    choose_item = choose_match

    Resolution = Enum('Resolution', 'REMOVE SKIP KEEPBOTH')

    default_resolution = 'REMOVE'

    def add_resolution(self, resolution):
        assert isinstance(resolution, self.Resolution)
        self._resolutions.append(resolution)

    def resolve_duplicate(self, task, found_duplicates):
        try:
            res = self._resolutions.pop(0)
        except IndexError:
            res = self.default_resolution

        if res == self.Resolution.SKIP:
            task.set_choice(importer.action.SKIP)
        elif res == self.Resolution.REMOVE:
            task.should_remove_duplicates = True
