import tempfile

from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.test import SimpleTestCase, override_settings

from executions.storage import ExecutionPayloadStore


class PayloadStorageTests(SimpleTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def test_round_trip_and_integrity(self):
        with override_settings(
            IDEAFLOW_EXECUTION_PAYLOAD_ROOT=self.tempdir.name,
            IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES=1024,
        ):
            store = ExecutionPayloadStore()
            payload = store.put("prompt", "secret text", content_type="text/plain")
            self.assertTrue(payload.reference.startswith("execution://"))
            self.assertEqual(store.get(payload.reference), b"secret text")
            self.assertTrue(store.verify(payload.reference, payload.sha256))

    def test_size_and_path_traversal_are_rejected(self):
        with override_settings(
            IDEAFLOW_EXECUTION_PAYLOAD_ROOT=self.tempdir.name,
            IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES=3,
        ):
            store = ExecutionPayloadStore()
            with self.assertRaises(ValidationError):
                store.put("prompt", b"four")
            with self.assertRaises(SuspiciousFileOperation):
                store.get("execution://../outside")
