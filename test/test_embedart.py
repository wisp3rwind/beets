# -*- coding: utf-8 -*-
# This file is part of beets.
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

from __future__ import division, absolute_import, print_function

import os.path
import shutil
from mock import patch
from functools import wraps

import test
from test import unittest

from beets.mediafile import MediaFile
from beets import config, logging, ui
from beets.util import syspath
from beets.util.artresizer import ArtResizer
from beets import art


def require_artresizer_compare(test):

    @wraps(test)
    def wrapper(*args, **kwargs):
        if not ArtResizer.shared.can_compare:
            raise unittest.SkipTest()
        else:
            return test(*args, **kwargs)

    return wrapper


class EmbedartCliTest(test.LibTestCase):

    small_artpath = os.path.join(test.RSRC, 'image-2x3.jpg')
    abbey_artpath = os.path.join(test.RSRC, 'abbey.jpg')
    abbey_similarpath = os.path.join(test.RSRC, 'abbey-similar.jpg')
    abbey_differentpath = os.path.join(test.RSRC, 'abbey-different.jpg')

    def setUp(self):
        super(EmbedartCliTest, self).setUp()
        self.load_plugins('embedart')

    def _read_image(self, artpath=None):
        if not artpath:
            artpath = self.small_artpath
        with open(syspath(artpath)) as f:
            return f.read()

    def test_embed_art_from_file(self):
        album = self.add_album_fixture()
        item = album.items()[0]
        self.run_command('embedart', '-f', self.small_artpath)
        mediafile = MediaFile(syspath(item.path))
        self.assertEqual(mediafile.images[0].data, self._read_image())

    def test_embed_art_from_album(self):
        album = self.add_album_fixture()
        item = album.items()[0]
        album.artpath = self.small_artpath
        album.store()
        self.run_command('embedart')
        mediafile = MediaFile(syspath(item.path))
        self.assertEqual(mediafile.images[0].data, self._read_image())

    def test_embed_art_remove_art_file(self):
        logging.getLogger('beets.embedart').setLevel(logging.DEBUG)

        tmp_path = os.path.join(self.temp_dir, 'copy.jpg')
        shutil.copy(self.small_artpath, tmp_path)

        album = self.add_album_fixture()
        album.artpath = tmp_path
        album.store()

        config['embedart']['remove_art_file'] = True
        self.run_command('embedart')

        if os.path.isfile(tmp_path):
            self.fail(u'Artwork file {0} was not deleted'.format(tmp_path))

    def test_art_file_missing(self):
        self.add_album_fixture()
        logging.getLogger('beets.embedart').setLevel(logging.DEBUG)
        with self.assertRaises(ui.UserError):
            self.run_command('embedart', '-f', '/doesnotexist')

    def test_embed_non_image_file(self):
        logging.getLogger('beets.embedart').setLevel(logging.DEBUG)

        album = self.add_album_fixture()

        tmp_path = os.path.join(self.temp_dir, 'copy.jpg')
        with open(tmp_path, 'w') as f:
            f.write(u'I am not an image.')

        self.run_command('embedart', '-f', tmp_path)

        mediafile = MediaFile(syspath(album.items()[0].path))
        self.assertFalse(mediafile.images)  # No image added.

    # TODO: slow
    @require_artresizer_compare
    def test_reject_different_art(self):
        album = self.add_album_fixture()
        item = album.items()[0]

        self.run_command('embedart', '-f', self.abbey_artpath)
        config['embedart']['compare_threshold'] = 20
        self.run_command('embedart', '-f', self.abbey_differentpath)

        mediafile = MediaFile(syspath(item.path))
        self.assertEqual(mediafile.images[0].data,
                         self._read_image(self.abbey_artpath),
                         u'Image written is not {0}'.format(
                             self.abbey_artpath))

    # TODO: slow
    @require_artresizer_compare
    def test_accept_similar_art(self):
        album = self.add_album_fixture()
        item = album.items()[0]

        self.run_command('embedart', '-f', self.abbey_artpath)
        config['embedart']['compare_threshold'] = 20
        self.run_command('embedart', '-f', self.abbey_similarpath)

        mediafile = MediaFile(syspath(item.path))
        self.assertEqual(mediafile.images[0].data,
                         self._read_image(self.abbey_similarpath),
                         u'Image written is not {0}'.format(
                             self.abbey_similarpath))

    def test_non_ascii_album_path(self):
        resource_path = os.path.join(test.RSRC, 'image.mp3').encode('utf8')
        album = self.add_album_fixture()
        trackpath = album.items()[0].path
        albumpath = album.path
        shutil.copy(syspath(resource_path), syspath(trackpath))

        self.run_command('extractart', '-n', 'extracted')

        self.assertExists(syspath(os.path.join(albumpath, b'extracted.png')))


@patch('beets.art.subprocess')
@patch('beets.art.extract')
@test.slow_test
class ArtSimilarityTest(unittest.TestCase):
    def test_imagemagick_response(self, mock_extract, mock_subprocess):
        mock_extract.return_value = True
        proc = mock_subprocess.Popen.return_value
        log = logging.getLogger('beets.embedart')

        # everything is fine
        proc.returncode = 0
        proc.communicate.return_value = "10", "tagada"
        self.assertTrue(art.check_art_similarity(log, None, None, 20))
        self.assertFalse(art.check_art_similarity(log, None, None, 5))

        # small failure
        proc.returncode = 1
        proc.communicate.return_value = "tagada", "10"
        self.assertTrue(art.check_art_similarity(log, None, None, 20))
        self.assertFalse(art.check_art_similarity(log, None, None, 5))

        # bigger failure
        proc.returncode = 2
        self.assertIsNone(art.check_art_similarity(log, None, None, 20))

        # IM result parsing problems
        proc.returncode = 0
        proc.communicate.return_value = "foo", "bar"
        self.assertIsNone(art.check_art_similarity(log, None, None, 20))

        proc.returncode = 1
        self.assertIsNone(art.check_art_similarity(log, None, None, 20))


def suite():
    return unittest.TestLoader().loadTestsFromName(__name__)

if __name__ == b'__main__':
    unittest.main(defaultTest='suite')
