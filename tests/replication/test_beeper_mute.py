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

from twisted.internet.testing import MemoryReactor

from synapse.api.constants import AccountDataTypes
from synapse.rest import admin
from synapse.rest.client import account_data, login, push_rule, room
from synapse.server import HomeServer
from synapse.storage.databases.main.account_data import BEEPER_MUTE_BACKFILL_UPDATE
from synapse.types import JsonDict
from synapse.util.clock import Clock
from synapse.util.json import json_encoder

from tests.replication._base import BaseMultiWorkerStreamTestCase
from tests.server import FakeChannel, make_request


class BeeperMuteWorkerTestCase(BaseMultiWorkerStreamTestCase):
    servlets = [
        admin.register_servlets,
        login.register_servlets,
        room.register_servlets,
        account_data.register_servlets,
        push_rule.register_servlets,
    ]

    def default_config(self) -> dict:
        conf = super().default_config()
        conf["stream_writers"] = {"account_data": ["writer"], "push_rules": ["writer"]}
        conf["instance_map"] = {
            "main": {"host": "testserv", "port": 8765},
            "writer": {"host": "testserv", "port": 1001},
        }
        return conf

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        self.user_id = self.register_user("user", "password")
        self.tok = self.login("user", "password")
        self.room_id = self.helper.create_room_as(self.user_id, tok=self.tok)

    def _make_writer(self) -> None:
        writer = self.make_worker_hs(
            "synapse.app.generic_worker", {"worker_name": "writer"}
        )
        self.writer_site = self._hs_to_site[writer]

    def _writer_request(
        self, method: str, path: str, content: JsonDict | None = None
    ) -> FakeChannel:
        return make_request(
            reactor=self.reactor,
            site=self.writer_site,
            method=method,
            path=path,
            content=content or {},
            access_token=self.tok,
        )

    def _get_rule(self) -> FakeChannel:
        return self._writer_request(
            "GET", f"/_matrix/client/v3/pushrules/global/room/{self.room_id}"
        )

    def _get_mute(self) -> FakeChannel:
        return self._writer_request(
            "GET",
            f"/_matrix/client/v3/user/{self.user_id}/rooms/{self.room_id}"
            f"/account_data/{AccountDataTypes.BEEPER_MUTE}",
        )

    def test_expiry_runs_on_background_worker(self) -> None:
        self._make_writer()
        channel = self._writer_request(
            "PUT",
            f"/_matrix/client/v3/user/{self.user_id}/rooms/{self.room_id}"
            f"/account_data/{AccountDataTypes.BEEPER_MUTE}",
            {"muted_until": self.clock.time_msec() + 120_000},
        )
        self.assertEqual(channel.code, 200, channel.result)
        self.assertEqual(self._get_rule().code, 200)

        for _ in range(240):
            self.reactor.advance(1)

        self.assertEqual(self._get_rule().code, 404)

    def test_backfill_writes_through_writer(self) -> None:
        self._make_writer()
        store = self.hs.get_datastores().main
        self.get_success(
            store.db_pool.simple_insert(
                "push_rules",
                {
                    "id": 1_000_000,
                    "user_name": self.user_id,
                    "rule_id": f"global/room/{self.room_id}",
                    "priority_class": 3,
                    "priority": 0,
                    "conditions": json_encoder.encode(
                        [
                            {
                                "kind": "event_match",
                                "key": "room_id",
                                "pattern": self.room_id,
                            }
                        ]
                    ),
                    "actions": "[]",
                },
            )
        )
        self.get_success(
            store.db_pool.simple_insert(
                "background_updates",
                {"update_name": BEEPER_MUTE_BACKFILL_UPDATE, "progress_json": "{}"},
            )
        )
        store.db_pool.updates._all_done = False
        self.wait_for_background_updates()

        channel = self._get_mute()
        self.assertEqual(channel.code, 200, channel.result)
        self.assertEqual(channel.json_body, {"muted_until": -1})
