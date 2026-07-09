from whisper_toggle.status_messages import startup_loading_notice


def test_startup_loading_notice_mentions_model_device_hotkey_and_not_ready():
    msg = startup_loading_notice(model="small.en", device="cuda", hotkey="ctrl+shift+h")

    assert "small.en" in msg
    assert "CUDA" in msg
    assert "ctrl+shift+h" in msg
    assert "not ready" in msg.lower()
    assert "Ready" in msg
