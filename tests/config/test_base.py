#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright 2020 The Matrix.org Foundation C.I.C.
# Copyright (C) 2023 New Vector, Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# See the GNU Affero General Public License for more details:
# <https://www.gnu.org/licenses/agpl-3.0.html>.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
#
# [This file includes modifications made by New Vector Limited]
#
#

import os.path
import tempfile
from unittest.mock import Mock

from synapse.config import ConfigError
from synapse.config._base import Config
from synapse.util.stringutils import random_string

from tests import unittest


class BaseConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # The root object needs a server property with a public_baseurl.
        root = Mock()
        root.server.public_baseurl = "http://test"
        self.config = Config(root)

    def test_loading_missing_templates(self) -> None:
        # Use a temporary directory that exists on the system, but that isn't likely to
        # contain template files
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Attempt to load an HTML template from our custom template directory
            template = self.config.read_templates(["sso_error.html"], (tmp_dir,))[0]

        # If no errors, we should've gotten the default template instead

        # Render the template
        a_random_string = random_string(5)
        html_content = template.render({"error_description": a_random_string})

        # Check that our string exists in the template
        self.assertIn(
            a_random_string,
            html_content,
            "Template file did not contain our test string",
        )

    def test_loading_custom_templates(self) -> None:
        # Use a temporary directory that exists on the system
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a temporary bogus template file
            with tempfile.NamedTemporaryFile(dir=tmp_dir) as tmp_template:
                # Get temporary file's filename
                template_filename = os.path.basename(tmp_template.name)

                # Write a custom HTML template
                contents = b"{{ test_variable }}"
                tmp_template.write(contents)
                tmp_template.flush()

                # Attempt to load the template from our custom template directory
                template = (
                    self.config.read_templates([template_filename], (tmp_dir,))
                )[0]

        # Render the template
        a_random_string = random_string(5)
        html_content = template.render({"test_variable": a_random_string})

        # Check that our string exists in the template
        self.assertIn(
            a_random_string,
            html_content,
            "Template file did not contain our test string",
        )

    def test_multiple_custom_template_directories(self) -> None:
        """Tests that directories are searched in the right order if multiple custom
        template directories are provided.
        """
        # Create two temporary directories on the filesystem.
        tempdirs = [
            tempfile.TemporaryDirectory(),
            tempfile.TemporaryDirectory(),
        ]

        # Create one template in each directory, whose content is the index of the
        # directory in the list.
        template_filename = "my_template.html.j2"
        for i in range(len(tempdirs)):
            tempdir = tempdirs[i]
            template_path = os.path.join(tempdir.name, template_filename)

            with open(template_path, "w") as fp:
                fp.write(str(i))
                fp.flush()

        # Retrieve the template.
        template = (
            self.config.read_templates(
                [template_filename],
                (td.name for td in tempdirs),
            )
        )[0]

        # Test that we got the template we dropped in the first directory in the list.
        self.assertEqual(template.render(), "0")

        # Add another template, this one only in the second directory in the list, so we
        # can test that the second directory is still searched into when no matching file
        # could be found in the first one.
        other_template_name = "my_other_template.html.j2"
        other_template_path = os.path.join(tempdirs[1].name, other_template_name)

        with open(other_template_path, "w") as fp:
            fp.write("hello world")
            fp.flush()

        # Retrieve the template.
        template = (
            self.config.read_templates(
                [other_template_name],
                (td.name for td in tempdirs),
            )
        )[0]

        # Test that the file has the expected content.
        self.assertEqual(template.render(), "hello world")

        # Cleanup the temporary directories manually since we're not using a context
        # manager.
        for td in tempdirs:
            td.cleanup()

    def test_loading_template_from_nonexistent_custom_directory(self) -> None:
        with self.assertRaises(ConfigError):
            self.config.read_templates(
                ["some_filename.html"], ("a_nonexistent_directory",)
            )
