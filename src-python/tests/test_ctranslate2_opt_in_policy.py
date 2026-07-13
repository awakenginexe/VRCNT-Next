import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import controller
from errors import ERROR_METADATA, ErrorCode


class CTranslate2OptInPolicyTests(unittest.TestCase):
    def test_translation_limit_does_not_advertise_automatic_local_fallback(self):
        metadata = ERROR_METADATA[ErrorCode.TRANSLATION_ENGINE_LIMIT]

        self.assertIs(metadata["auto_fallback"], False)

    def test_controller_has_no_legacy_forced_ctranslate2_fallback(self):
        self.assertFalse(hasattr(controller.Controller, "changeToCTranslate2Process"))


if __name__ == "__main__":
    unittest.main()
