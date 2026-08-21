#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright 2022 The Matrix.org Foundation C.I.C
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
import urllib.parse
from unittest.mock import AsyncMock

from twisted.internet.testing import MemoryReactor

from synapse.api.constants import ReceiptTypes
from synapse.api.errors import Codes
from synapse.rest import admin
from synapse.rest.client import account_data, login, room
from synapse.server import HomeServer
from synapse.types import JsonDict
from synapse.util.clock import Clock

from tests import unittest
from tests.server import FakeChannel


class AccountDataTestCase(unittest.HomeserverTestCase):
    servlets = [
        admin.register_servlets,
        login.register_servlets,
        room.register_servlets,
        account_data.register_servlets,
    ]

    def test_on_account_data_updated_callback(self) -> None:
        """Tests that the on_account_data_updated module callback is called correctly when
        a user's account data changes.
        """
        mocked_callback = AsyncMock(return_value=None)
        self.hs.get_account_data_handler()._on_account_data_updated_callbacks.append(
            mocked_callback
        )

        user_id = self.register_user("user", "password")
        tok = self.login("user", "password")
        account_data_type = "org.matrix.foo"
        account_data_content = {"bar": "baz"}

        # Change the user's global account data.
        channel = self.make_request(
            "PUT",
            f"/user/{user_id}/account_data/{account_data_type}",
            account_data_content,
            access_token=tok,
        )

        # Test that the callback is called with the user ID, the new account data, and
        # None as the room ID.
        self.assertEqual(channel.code, 200, channel.result)
        mocked_callback.assert_called_once_with(
            user_id, None, account_data_type, account_data_content
        )

        # Change the user's room-specific account data.
        room_id = self.helper.create_room_as(user_id, tok=tok)
        channel = self.make_request(
            "PUT",
            f"/user/{user_id}/rooms/{room_id}/account_data/{account_data_type}",
            account_data_content,
            access_token=tok,
        )

        # Test that the callback is called with the user ID, the room ID and the new
        # account data.
        self.assertEqual(channel.code, 200, channel.result)
        self.assertEqual(mocked_callback.call_count, 2)
        mocked_callback.assert_called_with(
            user_id, room_id, account_data_type, account_data_content
        )

    def test_beeper_inbox_state_endpoint(self) -> None:
        store = self.hs.get_datastores().main

        user_id = self.register_user("user", "password")
        tok = self.login("user", "password")

        room_id = self.helper.create_room_as(user_id, tok=tok)
        channel = self.make_request(
            "PUT",
            f"/_matrix/client/unstable/com.beeper.inbox/user/{user_id}/rooms/{room_id}/inbox_state",
            {},
            access_token=tok,
        )

        self.assertEqual(channel.code, 200, channel.result)
        self.assertIsNone(
            self.get_success(
                store.get_account_data_for_room_and_type(
                    user_id, room_id, "com.beeper.inbox.done"
                )
            )
        )
        self.assertIsNone(
            self.get_success(
                store.get_account_data_for_room_and_type(
                    user_id, room_id, "m.marked_unread"
                )
            )
        )

        before_ts = self.clock.time_msec()
        channel = self.make_request(
            "PUT",
            f"/_matrix/client/unstable/com.beeper.inbox/user/{user_id}/rooms/{room_id}/inbox_state",
            {"done": {"at_delta": 1000 * 60 * 5}, "marked_unread": True},
            access_token=tok,
        )
        after_ts = self.clock.time_msec()

        self.assertEqual(channel.code, 200, channel.result)

        done = self.get_success(
            store.get_account_data_for_room_and_type(
                user_id, room_id, "com.beeper.inbox.done"
            )
        )
        assert done is not None
        self.assertGreaterEqual(done["updated_ts"], before_ts)
        self.assertLessEqual(done["updated_ts"], after_ts)
        self.assertEqual(done["at_ts"], done["updated_ts"] + (1000 * 60 * 5))

        marked_unread = self.get_success(
            store.get_account_data_for_room_and_type(
                user_id, room_id, "m.marked_unread"
            )
        )
        assert marked_unread is not None
        self.assertEqual(marked_unread["unread"], True)
        self.assertEqual(marked_unread["ts"], done["updated_ts"])

    def test_beeper_inbox_state_endpoint_can_clear_unread(self) -> None:
        store = self.hs.get_datastores().main

        user_id = self.register_user("user", "password")
        tok = self.login("user", "password")

        room_id = self.helper.create_room_as(user_id, tok=tok)
        before_ts = self.clock.time_msec()
        channel = self.make_request(
            "PUT",
            f"/_matrix/client/unstable/com.beeper.inbox/user/{user_id}/rooms/{room_id}/inbox_state",
            {"marked_unread": False},
            access_token=tok,
        )
        after_ts = self.clock.time_msec()

        self.assertEqual(channel.code, 200, channel.result)

        self.assertEqual(channel.code, 200, channel.result)
        self.assertIsNone(
            self.get_success(
                store.get_account_data_for_room_and_type(
                    user_id, room_id, "com.beeper.inbox.done"
                )
            )
        )

        marked_unread = self.get_success(
            store.get_account_data_for_room_and_type(
                user_id, room_id, "m.marked_unread"
            )
        )
        assert marked_unread is not None
        self.assertEqual(marked_unread["unread"], False)
        self.assertGreaterEqual(marked_unread["ts"], before_ts)
        self.assertLessEqual(marked_unread["ts"], after_ts)

    def test_beeper_inbox_state_endpoint_can_set_read_marker(self) -> None:
        store = self.hs.get_datastores().main

        user_id = self.register_user("user", "password")
        tok = self.login("user", "password")

        room_id = self.helper.create_room_as(user_id, tok=tok)

        res = self.helper.send(room_id, "hello", tok=tok)

        existing_read_marker = self.get_success(
            store.get_account_data_for_room_and_type(
                user_id, room_id, ReceiptTypes.FULLY_READ
            )
        )

        channel = self.make_request(
            "PUT",
            f"/_matrix/client/unstable/com.beeper.inbox/user/{user_id}/rooms/{room_id}/inbox_state",
            {
                "read_markers": {
                    ReceiptTypes.FULLY_READ: res["event_id"],
                },
            },
            access_token=tok,
        )
        self.assertEqual(channel.code, 200)

        new_read_marker = self.get_success(
            store.get_account_data_for_room_and_type(
                user_id, room_id, ReceiptTypes.FULLY_READ
            )
        )
        self.assertNotEqual(existing_read_marker, new_read_marker)


class AccountDataCASTestCase(unittest.HomeserverTestCase):
    """Tests for the com.beeper.expect_revision_id compare-and-swap query param."""

    servlets = [
        admin.register_servlets,
        login.register_servlets,
        room.register_servlets,
        account_data.register_servlets,
    ]

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        self.store = hs.get_datastores().main
        self.user_id = self.register_user("user", "password")
        self.tok = self.login("user", "password")
        self.room_id = self.helper.create_room_as(self.user_id, tok=self.tok)

        # (name, PUT path) for both flavors of account data.
        self.endpoints = [
            ("global", f"/user/{self.user_id}/account_data"),
            ("room", f"/user/{self.user_id}/rooms/{self.room_id}/account_data"),
        ]

    def _put(
        self,
        base_path: str,
        account_data_type: str,
        content: JsonDict,
        expect_revision_id: str | None = None,
    ) -> FakeChannel:
        url = f"{base_path}/{account_data_type}"
        if expect_revision_id is not None:
            url += "?com.beeper.expect_revision_id=" + urllib.parse.quote(
                expect_revision_id
            )
        return self.make_request("PUT", url, content, access_token=self.tok)

    def _get_stored(self, name: str, account_data_type: str) -> JsonDict | None:
        if name == "global":
            content = self.get_success(
                self.store.get_global_account_data_by_type_for_user(
                    self.user_id, account_data_type
                )
            )
        else:
            content = self.get_success(
                self.store.get_account_data_for_room_and_type(
                    self.user_id, self.room_id, account_data_type
                )
            )
        return dict(content) if content is not None else None

    def test_no_param_always_writes(self) -> None:
        """Without the query param, writes succeed regardless of stored revision."""
        for name, path in self.endpoints:
            with self.subTest(endpoint=name):
                channel = self._put(
                    path, "org.example.foo", {"com.beeper.revision_id": "abc"}
                )
                self.assertEqual(channel.code, 200, channel.result)

                channel = self._put(path, "org.example.foo", {"bar": "baz"})
                self.assertEqual(channel.code, 200, channel.result)
                self.assertEqual(
                    self._get_stored(name, "org.example.foo"), {"bar": "baz"}
                )

    def test_expect_with_no_existing_data(self) -> None:
        """Any expected revision (including empty) matches when no data exists."""
        for name, path in self.endpoints:
            with self.subTest(endpoint=name):
                channel = self._put(
                    path, "org.example.new1", {"a": 1}, expect_revision_id="anything"
                )
                self.assertEqual(channel.code, 200, channel.result)

                channel = self._put(
                    path, "org.example.new2", {"a": 1}, expect_revision_id=""
                )
                self.assertEqual(channel.code, 200, channel.result)

    def test_expect_with_no_stored_revision(self) -> None:
        """Any expected revision matches when stored content lacks a revision id."""
        for name, path in self.endpoints:
            with self.subTest(endpoint=name):
                self._put(path, "org.example.foo", {"bar": "baz"})

                channel = self._put(
                    path, "org.example.foo", {"a": 1}, expect_revision_id="xyz"
                )
                self.assertEqual(channel.code, 200, channel.result)

    def test_expect_with_non_string_stored_revision(self) -> None:
        """A non-string stored revision id is treated as unset."""
        for name, path in self.endpoints:
            with self.subTest(endpoint=name):
                self._put(path, "org.example.foo", {"com.beeper.revision_id": 5})

                channel = self._put(
                    path, "org.example.foo", {"a": 1}, expect_revision_id="xyz"
                )
                self.assertEqual(channel.code, 200, channel.result)

    def test_expect_match(self) -> None:
        """A matching expected revision allows the write; new content need not
        carry a revision id itself."""
        for name, path in self.endpoints:
            with self.subTest(endpoint=name):
                self._put(
                    path,
                    "org.example.foo",
                    {"com.beeper.revision_id": "abc", "v": 1},
                )

                new_content = {"com.beeper.revision_id": "def", "v": 2}
                channel = self._put(
                    path, "org.example.foo", new_content, expect_revision_id="abc"
                )
                self.assertEqual(channel.code, 200, channel.result)
                self.assertEqual(self._get_stored(name, "org.example.foo"), new_content)

                # A revision id in the new content is not required.
                channel = self._put(
                    path, "org.example.foo", {"v": 3}, expect_revision_id="def"
                )
                self.assertEqual(channel.code, 200, channel.result)
                self.assertEqual(self._get_stored(name, "org.example.foo"), {"v": 3})

    def test_expect_mismatch(self) -> None:
        """A mismatched expected revision is rejected with 409 and the stored
        content is returned in the error body."""
        stored_content = {"com.beeper.revision_id": "abc", "v": 1}
        for name, path in self.endpoints:
            with self.subTest(endpoint=name):
                self._put(path, "org.example.foo", stored_content)

                channel = self._put(
                    path, "org.example.foo", {"v": 2}, expect_revision_id="xyz"
                )
                self.assertEqual(channel.code, 409, channel.result)
                self.assertEqual(
                    channel.json_body["errcode"], Codes.EXPECTED_REVISION_ID_MISMATCH
                )
                self.assertEqual(
                    channel.json_body["com.beeper.current_content"], stored_content
                )
                # The stored data is unchanged.
                self.assertEqual(
                    self._get_stored(name, "org.example.foo"), stored_content
                )

    def test_empty_expect_with_stored_revision(self) -> None:
        """An empty expected revision fails against an existing stored revision."""
        for name, path in self.endpoints:
            with self.subTest(endpoint=name):
                self._put(path, "org.example.foo", {"com.beeper.revision_id": "abc"})

                channel = self._put(
                    path, "org.example.foo", {"v": 2}, expect_revision_id=""
                )
                self.assertEqual(channel.code, 409, channel.result)
                self.assertEqual(
                    channel.json_body["errcode"], Codes.EXPECTED_REVISION_ID_MISMATCH
                )
