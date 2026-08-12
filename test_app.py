# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import re
import tempfile
import unittest

os.environ.update(
    PROXMOX_HOST="proxmox.example.com",
    PROXMOX_USER="allocator@pve",
    PROXMOX_TOKEN_NAME="allocator",
    PROXMOX_TOKEN_VALUE="test-token",
    ADMIN_PASSWORD="admin-password-for-tests",
    APP_SECRET_KEY="test-secret-key-that-is-longer-than-32-characters",
    SESSION_COOKIE_SECURE="false",
)

import app as allocator


class FakeUsers:
    def __init__(self):
        self.created = []
        self.deleted = []
        self.fail = False

    def post(self, **data):
        if self.fail:
            raise RuntimeError("user creation failed")
        self.created.append(data)

    def __call__(self, user_id):
        self.user_id = user_id
        return self

    def delete(self):
        self.deleted.append(self.user_id)


class FakeAcl:
    def __init__(self):
        self.grants = []
        self.fail = False

    def put(self, **data):
        if self.fail:
            raise RuntimeError("ACL failed")
        self.grants.append(data)


class FakeProxmox:
    def __init__(self):
        self.access = type("Access", (), {"users": FakeUsers(), "acl": FakeAcl()})()


class AllocatorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        allocator.LOG_FILE = os.path.join(self.temp_dir.name, "allocations.csv")
        allocator.proxmox = FakeProxmox()
        allocator.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = allocator.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf(self):
        page = self.client.get("/").get_data(as_text=True)
        return re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)

    def test_allocation_is_validated_and_recorded_once(self):
        token = self.csrf()
        invalid = self.client.post(
            "/allocate",
            data={"_csrf_token": token, "user_id": "사용자1"},
        )
        self.assertEqual(invalid.status_code, 400)

        response = self.client.post(
            "/allocate",
            data={"_csrf_token": token, "user_id": "alice1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(allocator.proxmox.access.users.created[0]["userid"], "alice1@pve")
        self.assertEqual(allocator.read_rows()[0]["Allocated_VMID"], "100")
        self.assertEqual(allocator.read_rows()[0]["Status"], "allocated")

        with self.client.session_transaction() as admin_session:
            admin_session["admin"] = True
        dashboard = self.client.get("/admin/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Allocated", dashboard.data)

        duplicate = self.client.post(
            "/allocate",
            data={"_csrf_token": token, "user_id": "alice1"},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(len(allocator.proxmox.access.users.created), 1)

    def test_remote_failure_fails_closed(self):
        allocator.proxmox.access.acl.fail = True
        response = self.client.post(
            "/allocate",
            data={"_csrf_token": self.csrf(), "user_id": "alice1"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(allocator.read_rows()[0]["Status"], "needs_review")
        self.assertEqual(allocator.proxmox.access.users.deleted, ["alice1@pve"])
        self.assertEqual(allocator.get_next_vmid({100}), 101)

    def test_user_creation_failure_releases_reservation(self):
        allocator.proxmox.access.users.fail = True
        response = self.client.post(
            "/allocate",
            data={"_csrf_token": self.csrf(), "user_id": "alice1"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(allocator.read_rows(), [])

    def test_post_requires_csrf(self):
        response = self.client.post(
            "/allocate",
            data={"user_id": "alice1"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
