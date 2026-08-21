#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright 2021 The Matrix.org Foundation C.I.C.
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

import json
from typing import Any, Callable, Iterable, Mapping
from unittest.mock import patch

from twisted.internet.testing import MemoryReactor

from synapse.api.constants import AccountDataTypes
from synapse.api.errors import Codes, SynapseError
from synapse.server import HomeServer
from synapse.storage.database import LoggingTransaction
from synapse.types import JsonDict
from synapse.util.clock import Clock

from tests import unittest


class IgnoredUsersTestCase(unittest.HomeserverTestCase):
    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        self.store = self.hs.get_datastores().main
        self.user = "@user:test"

    def _update_ignore_list(
        self, *ignored_user_ids: Iterable[str], ignorer_user_id: str | None = None
    ) -> None:
        """Update the account data to block the given users."""
        if ignorer_user_id is None:
            ignorer_user_id = self.user

        self.get_success(
            self.store.add_account_data_for_user(
                ignorer_user_id,
                AccountDataTypes.IGNORED_USER_LIST,
                {"ignored_users": {u: {} for u in ignored_user_ids}},
            )
        )

    def assert_ignorers(
        self, ignored_user_id: str, expected_ignorer_user_ids: set[str]
    ) -> None:
        self.assertEqual(
            self.get_success(self.store.ignored_by(ignored_user_id)),
            expected_ignorer_user_ids,
        )

    def assert_ignored(
        self, ignorer_user_id: str, expected_ignored_user_ids: set[str]
    ) -> None:
        self.assertEqual(
            self.get_success(self.store.ignored_users(ignorer_user_id)),
            expected_ignored_user_ids,
        )

    def test_ignoring_users(self) -> None:
        """Basic adding/removing of users from the ignore list."""
        self._update_ignore_list("@other:test", "@another:remote")
        self.assert_ignored(self.user, {"@other:test", "@another:remote"})

        # Check a user which no one ignores.
        self.assert_ignorers("@user:test", set())

        # Check a local user which is ignored.
        self.assert_ignorers("@other:test", {self.user})

        # Check a remote user which is ignored.
        self.assert_ignorers("@another:remote", {self.user})

        # Add one user, remove one user, and leave one user.
        self._update_ignore_list("@foo:test", "@another:remote")
        self.assert_ignored(self.user, {"@foo:test", "@another:remote"})

        # Check the removed user.
        self.assert_ignorers("@other:test", set())

        # Check the added user.
        self.assert_ignorers("@foo:test", {self.user})

        # Check the removed user.
        self.assert_ignorers("@another:remote", {self.user})

    def test_ignoring_self_fails(self) -> None:
        """Ensure users cannot add themselves to the ignored list."""

        f = self.get_failure(
            self.store.add_account_data_for_user(
                self.user,
                AccountDataTypes.IGNORED_USER_LIST,
                {"ignored_users": {self.user: {}}},
            ),
            SynapseError,
        ).value
        self.assertEqual(f.code, 400)
        self.assertEqual(f.errcode, Codes.INVALID_PARAM)

    def test_ignoring_bot_users(self) -> None:
        self._update_ignore_list("@other:test", "@another:remote")
        self.assert_ignored(self.user, {"@other:test", "@another:remote"})

        self._update_ignore_list("@other:test", "@another:remote", "@_other_bot:test")
        self.assert_ignored(self.user, {"@other:test", "@another:remote"})

        self._update_ignore_list("@iamnotabot:beeper.com")
        self.assert_ignored(self.user, {"@iamnotabot:beeper.com"})

        self._update_ignore_list("@_other_bot:beeper.com")
        self.assert_ignored(self.user, set())

        self._update_ignore_list("@whatsappbot:beeper.local")
        self.assert_ignored(self.user, set())

    def test_caching(self) -> None:
        """Ensure that caching works properly between different users."""
        # The first user ignores a user.
        self._update_ignore_list("@other:test")
        self.assert_ignored(self.user, {"@other:test"})
        self.assert_ignorers("@other:test", {self.user})

        # The second user ignores them.
        self._update_ignore_list("@other:test", ignorer_user_id="@second:test")
        self.assert_ignored("@second:test", {"@other:test"})
        self.assert_ignorers("@other:test", {self.user, "@second:test"})

        # The first user un-ignores them.
        self._update_ignore_list()
        self.assert_ignored(self.user, set())
        self.assert_ignorers("@other:test", {"@second:test"})

    def test_invalid_data(self) -> None:
        """Invalid data ends up clearing out the ignored users list."""
        # Add some data and ensure it is there.
        self._update_ignore_list("@other:test")
        self.assert_ignored(self.user, {"@other:test"})
        self.assert_ignorers("@other:test", {self.user})

        # No ignored_users key.
        self.get_success(
            self.store.add_account_data_for_user(
                self.user,
                AccountDataTypes.IGNORED_USER_LIST,
                {},
            )
        )

        # No one ignores the user now.
        self.assert_ignored(self.user, set())
        self.assert_ignorers("@other:test", set())

        # Add some data and ensure it is there.
        self._update_ignore_list("@other:test")
        self.assert_ignored(self.user, {"@other:test"})
        self.assert_ignorers("@other:test", {self.user})

        # Invalid data.
        self.get_success(
            self.store.add_account_data_for_user(
                self.user,
                AccountDataTypes.IGNORED_USER_LIST,
                {"ignored_users": "unexpected"},
            )
        )

        # No one ignores the user now.
        self.assert_ignored(self.user, set())
        self.assert_ignorers("@other:test", set())

    def test_ignoring_users_with_latest_stream_ids(self) -> None:
        """Test that ignoring users updates the latest stream ID for the ignored
        user list account data."""

        def get_latest_ignore_streampos(user_id: str) -> int | None:
            return self.get_success(
                self.store.get_latest_stream_id_for_global_account_data_by_type_for_user(
                    user_id, AccountDataTypes.IGNORED_USER_LIST
                )
            )

        self.assertIsNone(get_latest_ignore_streampos("@user:test"))

        self._update_ignore_list("@other:test", "@another:remote")

        self.assertEqual(get_latest_ignore_streampos("@user:test"), 2)

        # Add one user, remove one user, and leave one user.
        self._update_ignore_list("@foo:test", "@another:remote")

        self.assertEqual(get_latest_ignore_streampos("@user:test"), 3)


class RoomAccountDataCASTestCase(unittest.HomeserverTestCase):
    """Tests the compare-and-swap path of add_account_data_to_room, which was
    converted from an autocommit upsert to a transaction for this feature."""

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        self.store = self.hs.get_datastores().main
        self.user = "@user:test"
        self.room = "!room:test"
        self.data_type = "org.example.foo"

    def _get_stored(self) -> dict | None:
        content = self.get_success(
            self.store.get_account_data_for_room_and_type(
                self.user, self.room, self.data_type
            )
        )
        return dict(content) if content is not None else None

    def test_cas_match_writes_and_caches(self) -> None:
        self.get_success(
            self.store.add_account_data_to_room(
                self.user,
                self.room,
                self.data_type,
                {"com.beeper.revision_id": "abc", "v": 1},
            )
        )

        new_content = {"com.beeper.revision_id": "def", "v": 2}
        self.get_success(
            self.store.add_account_data_to_room(
                self.user,
                self.room,
                self.data_type,
                new_content,
                expected_revision_id="abc",
            )
        )
        self.assertEqual(self._get_stored(), new_content)

    def test_cas_mismatch_raises_and_leaves_data_unchanged(self) -> None:
        stored_content = {"com.beeper.revision_id": "abc", "v": 1}
        self.get_success(
            self.store.add_account_data_to_room(
                self.user, self.room, self.data_type, stored_content
            )
        )

        f = self.get_failure(
            self.store.add_account_data_to_room(
                self.user,
                self.room,
                self.data_type,
                {"v": 2},
                expected_revision_id="xyz",
            ),
            SynapseError,
        ).value
        self.assertEqual(f.code, 409)
        self.assertEqual(f.errcode, Codes.EXPECTED_REVISION_ID_MISMATCH)
        self.assertEqual(
            f.error_dict(None)["com.beeper.current_content"], stored_content
        )

        self.assertEqual(self._get_stored(), stored_content)

    def _racing_insert(self, competitor_content: JsonDict) -> Callable[..., bool]:
        """Returns a wrapper for the native upsert used by the CAS no-row
        branch that inserts a competitor row just before the first insert
        runs, simulating a concurrent first write winning the race."""
        real_insert = self.store.db_pool.simple_upsert_txn_native_upsert
        raced = False

        def racing_insert(
            txn: LoggingTransaction,
            table: str,
            keyvalues: Mapping[str, Any],
            values: Mapping[str, Any],
            insertion_values: Mapping[str, Any] | None = None,
            where_clause: str | None = None,
        ) -> bool:
            nonlocal raced
            if not raced:
                raced = True
                assert insertion_values is not None
                self.store.db_pool.simple_insert_txn(
                    txn,
                    table,
                    {
                        **keyvalues,
                        "stream_id": insertion_values["stream_id"],
                        "content": json.dumps(competitor_content),
                    },
                )
            return real_insert(
                txn, table, keyvalues, values, insertion_values, where_clause
            )

        return racing_insert

    def test_cas_first_write_race_mismatch(self) -> None:
        """A concurrent first write landing between the CAS existence check
        and our insert must be re-checked against the winning row."""
        competitor_content = {"com.beeper.revision_id": "theirs", "v": 1}
        with patch.object(
            self.store.db_pool,
            "simple_upsert_txn_native_upsert",
            self._racing_insert(competitor_content),
        ):
            f = self.get_failure(
                self.store.add_account_data_to_room(
                    self.user, self.room, self.data_type, {"v": 2}, "mine"
                ),
                SynapseError,
            ).value
        self.assertEqual(f.code, 409)
        self.assertEqual(f.errcode, Codes.EXPECTED_REVISION_ID_MISMATCH)
        self.assertEqual(
            f.error_dict(None)["com.beeper.current_content"], competitor_content
        )

    def test_cas_first_write_race_match(self) -> None:
        """If the concurrently-written row has no revision id, our expected
        revision still matches it and the write goes through."""
        with patch.object(
            self.store.db_pool,
            "simple_upsert_txn_native_upsert",
            self._racing_insert({"v": 1}),
        ):
            self.get_success(
                self.store.add_account_data_to_room(
                    self.user, self.room, self.data_type, {"v": 2}, "mine"
                )
            )
        self.assertEqual(self._get_stored(), {"v": 2})
