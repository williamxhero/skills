import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from task_backend import (
    BackendError,
    ProtocolError,
    TaskBackend,
    create_bootstrap_task,
    list_tasks,
    probe_app_server,
    probe_connector,
    read_applied_route,
    read_archive_state,
)
from task_identity import TaskIdentity


class FakeBackend:
    name = "host-task-api"

    def __init__(self):
        self.calls = []
        self.pages = [{"tasks": [{"formal_thread_id": "t-1"}], "next_cursor": "next"}, {"tasks": []}]

    def create_thread(self, **params):
        self.calls.append(("create_thread", params))
        return {"clientThreadId": "c-1", "formalThreadId": "t-1", "hostId": "h-1"}

    def read_thread(self, **params):
        self.calls.append(("read_thread", params))
        return {"formal_thread_id": "t-1", "host_id": "h-1", "task_id": "T1", "run_id": "R1", "attempt_id": "01", "owner_id": "o", "cwd": "C:/w", "project_id": "p", "lifecycle": "queued", "readback_evidence": ["thread/read"]}

    def list_tasks(self, **params):
        self.calls.append(("list_tasks", params))
        return self.pages.pop(0)

    def read_applied_route(self, **params):
        return {"model": "gpt-5.6-terra", "effort": "high"}

    def read_archive_state(self, **params):
        return {"archived": True, "evidence": ["archive/read"]}


class TaskBackendTests(unittest.TestCase):
    def identity(self):
        return TaskIdentity("T1", "R1", "01", "0123456789abcdef")

    def test_create_reads_formal_identity(self):
        backend = FakeBackend()
        registrations = []
        result = create_bootstrap_task(
            backend, "R1", self.identity(), "Implement issue", "gpt-5.6-terra", "high",
            owner_id="o", cwd="C:/w", project_id="p",
            project_id_source="saved_project_readback",
            project_canonical_path="C:/w",
            project_identity_evidence=["codex-app:list_projects:skills"],
            creation_intent={
                "status": "prepared", "operation": "create_thread",
                "run_id": "R1", "task_id": "T1", "attempt_id": "01",
                "evidence": ["controller:intent:1"],
            },
            register=lambda payload: registrations.append(payload) or {
                "status": "verified", "thread_id": "t-1",
                "evidence": ["controller:thread_registered:t-1"],
            },
        )
        self.assertEqual(result["formal_thread_id"], "t-1")
        self.assertEqual(result["identity_readback"]["lifecycle"], "queued")
        self.assertTrue(result["title"].startswith("[INN v=1 task=T1 run=R1 attempt=01"))
        create_params = backend.calls[0][1]
        self.assertEqual("saved_project_readback", create_params["project_id_source"])
        self.assertEqual("t-1", registrations[0]["formal_thread_id"])
        self.assertEqual("verified", result["registration_receipt"]["status"])

    def test_create_requires_persisted_intent_before_external_call(self):
        backend = FakeBackend()
        with self.assertRaises(BackendError):
            create_bootstrap_task(
                backend, "R1", self.identity(), "Implement issue",
                "gpt-5.6-terra", "high", owner_id="o", cwd="C:/w",
                project_id="p", creation_intent=None, register=lambda _: {},
            )
        self.assertEqual([], backend.calls)

    def test_create_fails_closed_when_registration_barrier_is_unverified(self):
        backend = FakeBackend()
        with self.assertRaises(BackendError):
            create_bootstrap_task(
                backend, "R1", self.identity(), "Implement issue",
                "gpt-5.6-terra", "high", owner_id="o", cwd="C:/w",
                project_id="p",
                creation_intent={
                    "status": "prepared", "operation": "create_thread",
                    "run_id": "R1", "task_id": "T1", "attempt_id": "01",
                    "evidence": ["controller:intent:1"],
                },
                register=lambda _: {"status": "failed", "evidence": []},
            )
        self.assertEqual("create_thread", backend.calls[0][0])

    def test_app_server_requires_confirmed_method_map(self):
        backend = TaskBackend("codex-app-server-jsonrpc", object(), {})
        with self.assertRaises(ProtocolError):
            list_tasks(backend)

    def test_listing_reports_inconclusive_when_page_limit_exhausted(self):
        backend = FakeBackend()
        result = list_tasks(backend, page_limit=1)
        self.assertEqual(result["reconciliation_status"], "inconclusive")
        self.assertEqual(result["next_cursor"], "next")

    def test_route_requires_both_applied_fields(self):
        class Bad(FakeBackend):
            def read_applied_route(self, **params):
                return {"model": "gpt-5.6-terra"}
        with self.assertRaises(BackendError):
            read_applied_route(Bad(), "t-1", "h-1")

    def test_formal_readback_requires_backend_evidence(self):
        class Bad(FakeBackend):
            def read_thread(self, **params):
                return {"formal_thread_id": "t-1", "host_id": "h-1", "task_id": "T1", "run_id": "R1", "attempt_id": "01", "owner_id": "o", "cwd": "C:/w", "project_id": "p"}
        from task_backend import read_task
        with self.assertRaises(BackendError):
            read_task(Bad(), "t-1", "h-1")

    def test_route_readback_requires_backend_evidence(self):
        class Bad(FakeBackend):
            def read_applied_route(self, **params):
                return {"model": "gpt-5.6-sol", "effort": "high"}
        with self.assertRaises(BackendError):
            read_applied_route(Bad(), "t-1", "h-1")

    def test_archive_requires_boolean_readback(self):
        class Bad(FakeBackend):
            def read_archive_state(self, **params):
                return {"archived": "true"}
        with self.assertRaises(BackendError):
            read_archive_state(Bad(), "t-1", "h-1")

    def test_connector_probe_requires_live_capabilities(self):
        class Connector:
            def request(self, operation, params):
                self.calls = getattr(self, "calls", []) + [(operation, params)]
                self.operation = operation
                self.operations = getattr(self, "operations", []) + [operation]
                if operation == "capabilities":
                    return {"operations": [
                    "create_thread", "list_tasks", "read_thread", "read_applied_route",
                    "send_message_to_thread", "set_thread_archived", "read_archive_state",
                    ], "formal_identity": True, "route_readback": True,
                    "probe_target": {"formal_thread_id": "t-1", "host_id": "h-1"}}
                if operation == "list_tasks":
                    return {"tasks": [{"formal_thread_id": "t-1", "host_id": "h-1"}]}
                if operation == "read_thread":
                    return {"formal_thread_id": "t-1", "host_id": "h-1", "task_id": "T1", "run_id": "R1", "attempt_id": "01", "owner_id": "o", "cwd": "C:/w", "project_id": "p", "lifecycle": "active", "readback_evidence": ["read"]}
                if operation == "read_applied_route":
                    return {"model": "gpt-5.6-sol", "effort": "high", "evidence": ["route"]}
                if operation == "read_archive_state":
                    return {"archived": False, "evidence": ["archive"]}
                return {"dry_run": True, "evidence": [operation]}
        connector = Connector()
        result = probe_connector(connector)
        self.assertEqual("allow", result["decision"])
        self.assertIn(connector.operation, {"create_thread", "send_message_to_thread", "set_thread_archived"})
        self.assertEqual({"capabilities", "list_tasks", "read_thread", "read_applied_route", "read_archive_state", "create_thread", "send_message_to_thread", "set_thread_archived"}, set(connector.operations))
        mutations = [params for operation, params in connector.calls if operation in {
            "create_thread", "send_message_to_thread", "set_thread_archived"
        }]
        self.assertEqual(3, len(mutations))
        self.assertTrue(all(params.get("dry_run") is True for params in mutations))

    def test_list_tasks_passes_recovery_indexes_to_backend(self):
        backend = FakeBackend()
        list_tasks(backend, title_prefix="[INN", client_thread_id="c-1", formal_thread_id="t-1")
        params = backend.calls[0][1]
        self.assertEqual("[INN", params["title_prefix"])
        self.assertEqual("c-1", params["client_thread_id"])
        self.assertEqual("t-1", params["formal_thread_id"])

    def test_app_server_probe_executes_every_operation(self):
        class Rpc:
            def __init__(self):
                self.calls = []

            def request(self, method, params):
                self.calls.append((method, params))
                if method == "initialize":
                    return {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "test"}}}
                if method == "list":
                    return {"result": {"tasks": [{"formal_thread_id": "t-1", "host_id": "h-1"}]}}
                if method == "read":
                    return {"result": {"formal_thread_id": "t-1", "host_id": "h-1", "task_id": "T1", "run_id": "R1", "attempt_id": "01", "owner_id": "o", "cwd": "C:/w", "project_id": "p", "lifecycle": "active", "readback_evidence": ["read"]}}
                if method == "route":
                    return {"result": {"model": "gpt-5.6-sol", "effort": "high", "evidence": ["route"]}}
                if method == "archive-read":
                    return {"result": {"archived": False, "evidence": ["archive"]}}
                return {"result": {"dry_run": True, "evidence": [method]}}

        rpc = Rpc()
        method_map = {"initialize": "initialize", "create_thread": "create", "list_tasks": "list", "read_thread": "read", "read_applied_route": "route", "send_message_to_thread": "send", "set_thread_archived": "archive", "read_archive_state": "archive-read", "probe_target": {"formal_thread_id": "t-1", "host_id": "h-1"}}
        result = probe_app_server(rpc, method_map)
        self.assertEqual("allow", result["decision"])
        self.assertEqual({"initialize", "list", "read", "route", "archive-read", "create", "send", "archive"}, {method for method, _ in rpc.calls})
        self.assertIn(("initialize", {
            "clientInfo": {"name": "implement-needs", "version": "1"},
            "capabilities": {},
        }), rpc.calls)

    def test_app_server_probe_rejects_without_live_operation_response(self):
        class Rpc:
            def request(self, method, params):
                if method == "initialize":
                    return {"result": {}}
                return {"error": {"code": -32601, "message": "missing"}}

        method_map = {name: name for name in ("initialize", "create_thread", "list_tasks", "read_thread", "read_applied_route", "send_message_to_thread", "set_thread_archived", "read_archive_state")}
        method_map["probe_target"] = {"formal_thread_id": "t-1", "host_id": "h-1"}
        with self.assertRaises(BackendError):
            probe_app_server(Rpc(), method_map)


if __name__ == "__main__":
    unittest.main()
