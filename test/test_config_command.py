# -*- coding: utf-8 -*-

from __future__ import division, absolute_import, print_function

import os
import yaml
from mock import patch

from beets import ui
from beets import config

import test
from test import unittest


class ConfigCommandTest(test.TestCase):

    def setUp(self):
        super(ConfigCommandTest, self).setUp()
        if 'EDITOR' in os.environ:
            self.editor = os.environ['EDITOR']
            del os.environ['EDITOR']

        self.config_path = os.path.join(self.temp_dir, 'config.yaml')
        with open(self.config_path, 'w') as file:
            file.write('library: lib\n')
            file.write('option: value\n')
            file.write('password: password_value')

        self.cli_config_path = os.path.join(self.temp_dir, 'cli_config.yaml')
        with open(self.cli_config_path, 'w') as file:
            file.write('option: cli overwrite')

        config.clear()
        config['password'].redact = True
        config._materialized = False

    def tearDown(self):
        super(ConfigCommandTest, self).tearDown()
        if hasattr(self, 'editor'):
            os.environ['EDITOR'] = self.editor

    def test_show_user_config(self):
        output = self.run_with_output('config', '-c')
        output = yaml.load(output)
        self.assertEqual(output['option'], 'value')
        self.assertEqual(output['password'], 'password_value')

    def test_show_user_config_with_defaults(self):
        output = self.run_with_output('config', '-dc')
        output = yaml.load(output)
        self.assertEqual(output['option'], 'value')
        self.assertEqual(output['password'], 'password_value')
        self.assertEqual(output['library'], 'lib')
        self.assertEqual(output['import']['timid'], False)

    def test_show_user_config_with_cli(self):
        output = self.run_with_output(
            '--config', self.cli_config_path, 'config')
        output = yaml.load(output)
        self.assertEqual(output['library'], 'lib')
        self.assertEqual(output['option'], 'cli overwrite')

    def test_show_redacted_user_config(self):
        output = self.run_with_output('config')
        output = yaml.load(output)
        self.assertEqual(output['option'], 'value')
        self.assertEqual(output['password'], 'REDACTED')

    def test_show_redacted_user_config_with_defaults(self):
        output = self.run_with_output('config', '-d')
        output = yaml.load(output)
        self.assertEqual(output['option'], 'value')
        self.assertEqual(output['password'], 'REDACTED')
        self.assertEqual(output['import']['timid'], False)

    def test_config_paths(self):
        output = self.run_with_output('config', '-p')
        paths = output.split('\n')
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0], self.config_path)

    def test_config_paths_with_cli(self):
        output = self.run_with_output(
            '--config', self.cli_config_path, 'config', '-p')
        paths = output.split('\n')
        self.assertEqual(len(paths), 3)
        self.assertEqual(paths[0], self.cli_config_path)

    def test_edit_config_with_editor_env(self):
        os.environ['EDITOR'] = 'myeditor'
        with patch('os.execlp') as execlp:
            self.run_command('config', '-e')
        execlp.assert_called_once_with(
            'myeditor', 'myeditor', self.config_path)

    def test_edit_config_with_automatic_open(self):
        with patch('beets.util.open_anything') as open:
            open.return_value = 'please_open'
            with patch('os.execlp') as execlp:
                self.run_command('config', '-e')
        execlp.assert_called_once_with(
            'please_open', 'please_open', self.config_path)

    def test_config_editor_not_found(self):
        with self.assertRaises(ui.UserError) as user_error:
            with patch('os.execlp') as execlp:
                execlp.side_effect = OSError('here is problem')
                self.run_command('config', '-e')
        self.assertIn('Could not edit configuration',
                      unicode(user_error.exception))
        self.assertIn('here is problem', unicode(user_error.exception))

    def test_edit_invalid_config_file(self):
        with open(self.config_path, 'w') as file:
            file.write('invalid: [')
        # config.clear()
        # config._materialized = False

        os.environ['EDITOR'] = 'myeditor'
        with patch('os.execlp') as execlp:
            self.run_command('config', '-e')
        execlp.assert_called_once_with(
            'myeditor', 'myeditor', self.config_path)


def suite():
    return unittest.TestLoader().loadTestsFromName(__name__)

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
