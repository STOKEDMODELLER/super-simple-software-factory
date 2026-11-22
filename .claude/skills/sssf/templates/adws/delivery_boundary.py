"""One named Docker container per execution generation. No host credentials or socket mounts."""
import json
import os
import subprocess


class Docker:
    @staticmethod
    def cli(*args, timeout=20):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[-1000:])
        return result.stdout.strip()

    def image(self, image):
        return self.cli("image", "inspect", "--format", "{{.Id}}", image)

    def inspect(self, name, owner):
        try:
            raw = self.cli("container", "inspect", "--format",
                           '{{json .Config.Labels}}|{{json .State}}', name)
        except RuntimeError as exc:
            if "No such container" in str(exc) or "No such object" in str(exc):
                return None
            raise
        labels, state = raw.split("|", 1)
        if json.loads(labels).get("sssf.delivery") != owner:
            raise RuntimeError("boundary identity mismatch")
        return json.loads(state)

    def create(self, name, owner, image, path, argv, env, network):
        options = ["create", "--name", name, "--label", f"sssf.delivery={owner}",
                   "--init", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--read-only", "--tmpfs", "/tmp:rw,nosuid", "--pids-limit=256",
                   "--log-driver=json-file", "--log-opt=max-size=1m", "--log-opt=max-file=1", "--network", network,
                   "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
                   "--mount", f"type=bind,src={path},dst=/workspace", "-w", "/workspace"]
        for key, value in env.items():
            options.extend(("-e", f"{key}={value}"))
        self.cli(*options, "--entrypoint", argv[0], image, *argv[1:])

    def start(self, name):
        self.cli("start", name)

    def destroy(self, name, owner):
        state = self.inspect(name, owner)
        if state is None:
            return
        if state["Running"]:
            self.cli("stop", "--time", "2", name)
        self.cli("rm", "--force", "--volumes", name)
        if self.inspect(name, owner) is not None:
            raise RuntimeError("container removal unconfirmed")
