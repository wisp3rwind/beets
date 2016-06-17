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

import re
import os.path

import test
from test import unittest, control_stdin

from beets.mediafile import MediaFile
from beets import config, util
from beets import ui


class TestHelper(unittest.TestCase):

    def tagged_copy_cmd(self, tag):
        """Return a conversion command that copies files and appends
        `tag` to the copy.
        """
        if re.search('[^a-zA-Z0-9]', tag):
            raise ValueError(u"tag '{0}' must only contain letters and digits"
                             .format(tag))

        # A Python script that copies the file and appends a tag.
        stub = os.path.join(test.RSRC, b'convert_stub.py').decode('utf-8')
        return u"python '{}' $source $dest {}".format(stub, tag)

    def assertFileTag(self, path, tag):  # noqa
        """Assert that the path is a file and the files content ends with `tag`.
        """
        tag = tag.encode('utf-8')
        self.assertTrue(os.path.isfile(path),
                        u'{0} is not a file'.format(path))
        with open(path, 'rb') as f:
            f.seek(-len(tag), os.SEEK_END)
            self.assertEqual(f.read(), tag,
                             u'{0} is not tagged with {1}'.format(path, tag))

    def assertNoFileTag(self, path, tag):  # noqa
        """Assert that the path is a file and the files content does not
        end with `tag`.
        """
        tag = tag.encode('utf-8')
        self.assertTrue(os.path.isfile(path),
                        u'{0} is not a file'.format(path))
        with open(path, 'rb') as f:
            f.seek(-len(tag), os.SEEK_END)
            self.assertNotEqual(f.read(), tag,
                                u'{0} is unexpectedly tagged with {1}'
                                .format(path, tag))


@test.slow_test()
class ImportConvertTest(test.LibTestCase, TestHelper):

    def setUp(self):
        super(ImportConvertTest, self).setUp(disk=True)  # Converter is threaded
        self.importer = self.create_importer()
        self.load_plugins('convert')

        config['convert'] = {
            'dest': os.path.join(self.temp_dir, b'convert'),
            'command': self.tagged_copy_cmd('convert'),
            # Enforce running convert
            'max_bitrate': 1,
            'auto': True,
            'quiet': False,
        }

    def test_import_converted(self):
        self.importer.run()
        item = self.lib.items().get()
        self.assertFileTag(item.path, 'convert')

    def test_import_original_on_convert_error(self):
        # `false` exits with non-zero code
        config['convert']['command'] = u'false'
        self.importer.run()

        item = self.lib.items().get()
        self.assertIsNotNone(item)
        self.assertTrue(os.path.isfile(item.path))


class ConvertCommand(object):
    """A mixin providing a utility method to run the `convert`command
    in tests.
    """
    def run_convert_path(self, path, *args):
        """Run the `convert` command on a given path."""
        # The path is currently a filesystem bytestring. Convert it to
        # an argument bytestring.
        path = path.decode(util._fsencoding()).encode(ui._arg_encoding())

        args = args + (b'path:' + path,)
        return self.run_command('convert', *args)

    def run_convert(self, *args):
        """Run the `convert` command on `self.item`."""
        return self.run_convert_path(self.item.path, *args)


@test.slow_test()
class ConvertCliTest(test.LibTestCase, TestHelper, ConvertCommand):

    def setUp(self):
        super(ConvertCliTest, self).setUp(disk=True)  # Converter is threaded
        self.album = self.add_album_fixture(ext='ogg')
        self.item = self.album.items()[0]
        self.load_plugins('convert')

        self.convert_dest = util.bytestring_path(
            os.path.join(self.temp_dir, b'convert_dest')
        )
        config['convert'] = {
            'dest': self.convert_dest,
            'paths': {'default': 'converted'},
            'format': 'mp3',
            'formats': {
                'mp3': self.tagged_copy_cmd('mp3'),
                'opus': {
                    'command': self.tagged_copy_cmd('opus'),
                    'extension': 'ops',
                }
            }
        }

    def test_convert(self):
        with control_stdin('y'):
            self.run_convert()
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        self.assertFileTag(converted, 'mp3')

    def test_convert_with_auto_confirmation(self):
        self.run_convert('--yes')
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        self.assertFileTag(converted, 'mp3')

    def test_rejecet_confirmation(self):
        with control_stdin('n'):
            self.run_convert()
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        self.assertFalse(os.path.isfile(converted))

    def test_convert_keep_new(self):
        self.assertEqual(os.path.splitext(self.item.path)[1], b'.ogg')

        with control_stdin('y'):
            self.run_convert('--keep-new')

        self.item.load()
        self.assertEqual(os.path.splitext(self.item.path)[1], b'.mp3')

    def test_format_option(self):
        with control_stdin('y'):
            self.run_convert('--format', 'opus')
            converted = os.path.join(self.convert_dest, b'converted.ops')
        self.assertFileTag(converted, 'opus')

    def test_embed_album_art(self):
        config['convert']['embed'] = True
        image_path = os.path.join(test.RSRC, b'image-2x3.jpg')
        self.album.artpath = image_path
        self.album.store()
        with open(os.path.join(image_path), 'rb') as f:
            image_data = f.read()

        with control_stdin('y'):
            self.run_convert()
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        mediafile = MediaFile(converted)
        self.assertEqual(mediafile.images[0].data, image_data)

    def test_skip_existing(self):
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        self.touch(converted, content='XXX')
        self.run_convert('--yes')
        with open(converted, 'r') as f:
            self.assertEqual(f.read(), 'XXX')

    def test_pretend(self):
        self.run_convert('--pretend')
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        self.assertFalse(os.path.exists(converted))


@test.slow_test()
class NeverConvertLossyFilesTest(test.LibTestCase, TestHelper,
                                 ConvertCommand):
    """Test the effect of the `never_convert_lossy_files` option.
    """

    def setUp(self):
        super(NeverConvertLossyFilesTest, self).setUp(disk=True)  # Converter is threaded
        self.load_plugins('convert')

        self.convert_dest = os.path.join(self.temp_dir, b'convert_dest')
        config['convert'] = {
            'dest': self.convert_dest,
            'paths': {'default': 'converted'},
            'never_convert_lossy_files': True,
            'format': 'mp3',
            'formats': {
                'mp3': self.tagged_copy_cmd('mp3'),
            }
        }

    def test_transcode_from_lossles(self):
        [item] = self.add_item_fixtures(ext='flac')
        with control_stdin('y'):
            self.run_convert_path(item.path)
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        self.assertFileTag(converted, 'mp3')

    def test_transcode_from_lossy(self):
        config['convert']['never_convert_lossy_files'] = False
        [item] = self.add_item_fixtures(ext='ogg')
        with control_stdin('y'):
            self.run_convert_path(item.path)
        converted = os.path.join(self.convert_dest, b'converted.mp3')
        self.assertFileTag(converted, 'mp3')

    def test_transcode_from_lossy_prevented(self):
        [item] = self.add_item_fixtures(ext='ogg')
        with control_stdin('y'):
            self.run_convert_path(item.path)
        converted = os.path.join(self.convert_dest, b'converted.ogg')
        self.assertNoFileTag(converted, 'mp3')


def suite():
    return unittest.TestLoader().loadTestsFromName(__name__)

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
