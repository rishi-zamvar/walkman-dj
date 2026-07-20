"""ZeeChat phone ping wrapper."""
import subprocess
import os

SCRIPT = os.path.expanduser("~/.claude/skills/zeechat/scripts/ping.sh")


def ping(msg, runner=None):
    cmd = ["bash", SCRIPT, msg]
    (runner or (lambda c: subprocess.run(c)))(cmd)
