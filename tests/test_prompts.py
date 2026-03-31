from prompts import CALL_TYPE_CONFIG


EXPECTED_CALL_TYPES = [
    "screening_call",
    "reference_check",
    "interview_prep",
    "post_interview_rundown",
    "offer_extension",
    "general_recruiter_call",
]

REQUIRED_KEYS = {"label", "system_prompt", "developer_prompt"}


class TestCallTypeConfig:
    def test_all_call_types_present(self):
        for ct in EXPECTED_CALL_TYPES:
            assert ct in CALL_TYPE_CONFIG, f"Missing call type: {ct}"

    def test_no_unexpected_call_types(self):
        assert set(CALL_TYPE_CONFIG.keys()) == set(EXPECTED_CALL_TYPES)

    def test_each_type_has_required_keys(self):
        for ct, cfg in CALL_TYPE_CONFIG.items():
            for key in REQUIRED_KEYS:
                assert key in cfg, f"{ct} missing key: {key}"

    def test_labels_are_nonempty_strings(self):
        for ct, cfg in CALL_TYPE_CONFIG.items():
            assert isinstance(cfg["label"], str)
            assert len(cfg["label"]) > 0, f"{ct} has empty label"

    def test_prompts_are_nonempty_strings(self):
        for ct, cfg in CALL_TYPE_CONFIG.items():
            assert isinstance(cfg["system_prompt"], str)
            assert len(cfg["system_prompt"]) > 50, f"{ct} system_prompt too short"
            assert isinstance(cfg["developer_prompt"], str)
            assert len(cfg["developer_prompt"]) > 50, f"{ct} developer_prompt too short"
