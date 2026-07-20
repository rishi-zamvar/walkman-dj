from walkman_dj.notify import ping
def test_ping_calls_script_with_message():
    calls=[]
    ping("done: 3200 tracks", runner=lambda cmd: calls.append(cmd))
    assert calls and "done: 3200 tracks" in " ".join(calls[0])
