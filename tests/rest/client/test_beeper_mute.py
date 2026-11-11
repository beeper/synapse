#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright (C) 2026 Beeper
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# See the GNU Affero General Public License for more details:
# <https://www.gnu.org/licenses/agpl-3.0.html>.
#

from unittest.mock import AsyncMock

from twisted.internet.testing import MemoryReactor

from synapse.api.constants import AccountDataTypes
from synapse.rest import admin
from synapse.rest.client import (
    account_data,
    login,
    push_rule,
    room,
    room_upgrade_rest_servlet,
)
from synapse.server import HomeServer
from synapse.storage.databases.main.account_data import (
    BEEPER_MUTE_BACKFILL_UPDATE,
    BEEPER_MUTE_EXPIRY_ACTION,
)
from synapse.types import JsonDict, TaskStatus
from synapse.util.clock import Clock
from synapse.util.json import json_encoder

from tests import unittest

UNHOSTED_ROOM_ID = "!unhosted:example.com"


class BeeperMuteTestCase(unittest.HomeserverTestCase):
    servlets = [
        admin.register_servlets,
        login.register_servlets,
        room.register_servlets,
        account_data.register_servlets,
        push_rule.register_servlets,
        room_upgrade_rest_servlet.register_servlets,
    ]

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        self.store = hs.get_datastores().main
        self.user_id = self.register_user("user", "password")
        self.tok = self.login("user", "password")
        self.room_id = self.helper.create_room_as(self.user_id, tok=self.tok)

    def _put_mute(self, room_id: str, content: JsonDict) -> None:
        channel = self.make_request(
            "PUT",
            f"/user/{self.user_id}/rooms/{room_id}/account_data/{AccountDataTypes.BEEPER_MUTE}",
            content,
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)

    def _get_mute(self, room_id: str) -> JsonDict | None:
        channel = self.make_request(
            "GET",
            f"/user/{self.user_id}/rooms/{room_id}/account_data/{AccountDataTypes.BEEPER_MUTE}",
            access_token=self.tok,
        )
        if channel.code == 404:
            return None
        self.assertEqual(channel.code, 200, channel.result)
        return channel.json_body

    def _put_rule(self, room_id: str, actions: list) -> None:
        channel = self.make_request(
            "PUT",
            f"/pushrules/global/room/{room_id}",
            {"actions": actions},
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)

    def _get_rule(self, room_id: str) -> JsonDict | None:
        channel = self.make_request(
            "GET",
            f"/pushrules/global/room/{room_id}",
            access_token=self.tok,
        )
        if channel.code == 404:
            return None
        self.assertEqual(channel.code, 200, channel.result)
        return channel.json_body

    def test_mute_account_data_writes_push_rule(self) -> None:
        self._put_mute(self.room_id, {"muted_until": -1})
        rule = self._get_rule(self.room_id)
        assert rule is not None
        self.assertEqual(rule["actions"], [])
        self.assertTrue(rule["enabled"])

        self._put_mute(self.room_id, {"muted_until": 0})
        self.assertIsNone(self._get_rule(self.room_id))

        self._put_mute(self.room_id, {"muted_until": self.clock.time_msec() + 60_000})
        rule = self._get_rule(self.room_id)
        assert rule is not None
        self.assertEqual(rule["actions"], [])

        self._put_mute(self.room_id, {"muted_until": self.clock.time_msec() - 1})
        self.assertIsNone(self._get_rule(self.room_id))

        self._put_mute(self.room_id, {"muted_until": -1})
        self._put_mute(self.room_id, {})
        self.assertIsNone(self._get_rule(self.room_id))

    def test_mute_account_data_enables_disabled_rule(self) -> None:
        self._put_rule(self.room_id, ["notify"])
        channel = self.make_request(
            "PUT",
            f"/pushrules/global/room/{self.room_id}/enabled",
            {"enabled": False},
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)

        self._put_mute(self.room_id, {"muted_until": -1})
        rule = self._get_rule(self.room_id)
        assert rule is not None
        self.assertEqual(rule["actions"], [])
        self.assertTrue(rule["enabled"])

    def test_mute_account_data_unmute_keeps_unmuted_rule(self) -> None:
        actions = ["notify", {"set_tweak": "sound", "value": "ping"}]
        self._put_rule(self.room_id, actions)
        self._put_mute(self.room_id, {"muted_until": 0})
        rule = self._get_rule(self.room_id)
        assert rule is not None
        self.assertEqual(rule["actions"], actions)

        self._put_rule(self.room_id, [])
        channel = self.make_request(
            "PUT",
            f"/pushrules/global/room/{self.room_id}/enabled",
            {"enabled": False},
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)
        self._put_mute(self.room_id, {"muted_until": 0})
        rule = self._get_rule(self.room_id)
        assert rule is not None
        self.assertFalse(rule["enabled"])

    def test_push_rule_writes_mute_account_data(self) -> None:
        self._put_rule(self.room_id, [])
        self.assertEqual(self._get_mute(self.room_id), {"muted_until": -1})

        self._put_rule(self.room_id, ["notify"])
        self.assertEqual(self._get_mute(self.room_id), {"muted_until": 0})

        channel = self.make_request(
            "PUT",
            f"/pushrules/global/room/{self.room_id}/actions",
            {"actions": ["dont_notify"]},
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)
        self.assertEqual(self._get_mute(self.room_id), {"muted_until": -1})

        channel = self.make_request(
            "PUT",
            f"/pushrules/global/room/{self.room_id}/enabled",
            {"enabled": False},
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)
        self.assertEqual(self._get_mute(self.room_id), {"muted_until": 0})

        channel = self.make_request(
            "PUT",
            f"/pushrules/global/room/{self.room_id}/enabled",
            {"enabled": True},
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)
        self.assertEqual(self._get_mute(self.room_id), {"muted_until": -1})

        channel = self.make_request(
            "DELETE",
            f"/pushrules/global/room/{self.room_id}",
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)
        self.assertEqual(self._get_mute(self.room_id), {"muted_until": 0})

    def test_push_rule_unmuted_without_account_data_writes_nothing(self) -> None:
        self._put_rule(self.room_id, ["notify"])
        self.assertIsNone(self._get_mute(self.room_id))

    def test_push_rule_keeps_timed_mute(self) -> None:
        muted_until = self.clock.time_msec() + 3_600_000
        self._put_mute(self.room_id, {"muted_until": muted_until})

        self._put_rule(self.room_id, [])
        self.assertEqual(self._get_mute(self.room_id), {"muted_until": muted_until})

    def test_unhosted_room_ignored(self) -> None:
        self._put_rule(UNHOSTED_ROOM_ID, [])
        self.assertIsNone(self._get_mute(UNHOSTED_ROOM_ID))

        channel = self.make_request(
            "DELETE",
            f"/pushrules/global/room/{UNHOSTED_ROOM_ID}",
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)

        self._put_mute(UNHOSTED_ROOM_ID, {"muted_until": -1})
        self.assertIsNone(self._get_rule(UNHOSTED_ROOM_ID))

        self._put_rule(UNHOSTED_ROOM_ID, ["notify"])
        self.assertEqual(self._get_mute(UNHOSTED_ROOM_ID), {"muted_until": -1})

        self._put_mute(UNHOSTED_ROOM_ID, {"muted_until": 0})
        rule = self._get_rule(UNHOSTED_ROOM_ID)
        assert rule is not None
        self.assertEqual(rule["actions"], ["notify"])

    def test_timed_mute_expires(self) -> None:
        muted_until = self.clock.time_msec() + 120_000
        content = {"muted_until": muted_until}
        self._put_mute(self.room_id, content)
        self.assertIsNotNone(self._get_rule(self.room_id))

        tasks = self.get_success(
            self.store.get_scheduled_tasks(
                actions=[BEEPER_MUTE_EXPIRY_ACTION],
                statuses=[TaskStatus.SCHEDULED],
            )
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].timestamp, muted_until)
        self.assertEqual(
            tasks[0].params, {"user_id": self.user_id, "room_id": self.room_id}
        )

        self.reactor.advance(60)
        self.assertIsNotNone(self._get_rule(self.room_id))

        for _ in range(3):
            self.reactor.advance(60)
        self.assertIsNone(self._get_rule(self.room_id))
        self.assertEqual(self._get_mute(self.room_id), content)

    def test_failed_expiry_is_rescheduled(self) -> None:
        handler = self.hs.get_account_data_handler()
        handler.expire_beeper_mute = AsyncMock(side_effect=Exception("boom"))  # type: ignore[method-assign]

        self._put_mute(self.room_id, {"muted_until": self.clock.time_msec() + 60_000})
        for _ in range(3):
            self.reactor.advance(60)

        tasks = self.get_success(
            self.store.get_scheduled_tasks(
                actions=[BEEPER_MUTE_EXPIRY_ACTION],
                statuses=[TaskStatus.SCHEDULED],
            )
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            tasks[0].params, {"user_id": self.user_id, "room_id": self.room_id}
        )

    def test_room_upgrade_keeps_timed_mute(self) -> None:
        content = {"muted_until": self.clock.time_msec() + 3_600_000}
        self._put_mute(self.room_id, content)

        channel = self.make_request(
            "POST",
            f"/_matrix/client/v3/rooms/{self.room_id}/upgrade",
            {"new_version": "10"},
            access_token=self.tok,
        )
        self.assertEqual(channel.code, 200, channel.result)
        new_room_id = channel.json_body["replacement_room"]

        self.assertEqual(self._get_mute(new_room_id), content)
        rule = self._get_rule(new_room_id)
        assert rule is not None
        self.assertEqual(rule["actions"], [])

    def test_expiry_ignores_replaced_mute(self) -> None:
        self._put_mute(self.room_id, {"muted_until": self.clock.time_msec() + 120_000})
        self._put_mute(self.room_id, {"muted_until": -1})

        for _ in range(4):
            self.reactor.advance(60)
        rule = self._get_rule(self.room_id)
        assert rule is not None
        self.assertEqual(rule["actions"], [])

    def test_backfill(self) -> None:
        hosted_without_mute = self.helper.create_room_as(self.user_id, tok=self.tok)
        hosted_not_muting = self.helper.create_room_as(self.user_id, tok=self.tok)
        hosted_timed = self.helper.create_room_as(self.user_id, tok=self.tok)
        muted_until = self.clock.time_msec() + 3_600_000

        self._put_mute(hosted_timed, {"muted_until": muted_until})

        for push_rule_id, (room_id, actions) in enumerate(
            [
                (hosted_without_mute, []),
                (hosted_not_muting, ["notify"]),
                (UNHOSTED_ROOM_ID, ["dont_notify"]),
            ],
            start=1_000_000,
        ):
            self.get_success(
                self.store.db_pool.simple_insert(
                    "push_rules",
                    {
                        "id": push_rule_id,
                        "user_name": self.user_id,
                        "rule_id": f"global/room/{room_id}",
                        "priority_class": 3,
                        "priority": 0,
                        "conditions": json_encoder.encode(
                            [
                                {
                                    "kind": "event_match",
                                    "key": "room_id",
                                    "pattern": room_id,
                                }
                            ]
                        ),
                        "actions": json_encoder.encode(actions),
                    },
                )
            )

        self.get_success(
            self.store.db_pool.simple_insert(
                "background_updates",
                {"update_name": BEEPER_MUTE_BACKFILL_UPDATE, "progress_json": "{}"},
            )
        )
        self.store.db_pool.updates._all_done = False
        self.wait_for_background_updates()

        self.assertEqual(self._get_mute(hosted_without_mute), {"muted_until": -1})
        self.assertIsNone(self._get_mute(hosted_not_muting))
        self.assertIsNone(self._get_mute(UNHOSTED_ROOM_ID))
        self.assertEqual(self._get_mute(hosted_timed), {"muted_until": muted_until})
