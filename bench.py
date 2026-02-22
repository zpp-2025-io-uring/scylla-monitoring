#!/usr/bin/env python3
import os
import sys
import yaml
import subprocess
import argparse
import signal
import atexit
import time

# path to the scylla binary used for launching nodes
scylla_dir = "/home/marcinsz/scylladb/build/dev/scylla"

# parent for relative work directories, output will be placed inside each node's workdir
output_parent_dir = f"./workdirs/{time.strftime('%Y%m%d-%H%M%S')}"

scylla_servers_file = "/home/marcinsz/scylla-monitoring/prometheus/scylla_servers.yml"


class ConfigBuilder:
    """Builds a scylla.yaml string from the template.
    """

    def __init__(self, work_dir, seeds, native_transport_port, native_shard_aware_transport_port):
        self.work_dir = work_dir
        self.seeds = seeds
        self.native_transport_port = native_transport_port
        self.native_shard_aware_transport_port = native_shard_aware_transport_port

    def build(self):
        with open("config.template.yaml", "r") as f:
            config_template = f.read()

        config = (
            config_template
            .replace("{WORK_DIR}", self.work_dir)
            .replace("{SEEDS}", self.seeds)
            .replace("{NATIVE_TRANSPORT_PORT}", str(self.native_transport_port))
            .replace("{NATIVE_SHARD_AWARE_TRANSPORT_PORT}", str(self.native_shard_aware_transport_port))

        )

        return config


class ScyllaNode:
    """Represents a single scylla instance that can be started and stopped."""

    def __init__(self, name, api_port, work_dir, seeds, listen_address, native_transport_port, native_shard_aware_transport_port, extra_scylla_command_line_args=[]):
        self.name = name
        self.listen_address = listen_address
        self.api_port = api_port
        self.work_dir = work_dir
        self.seeds = seeds
        self.native_transport_port = native_transport_port
        self.native_shard_aware_transport_port = native_shard_aware_transport_port
        self.extra_scylla_command_line_args = extra_scylla_command_line_args
        self.process = None

        # ensure output directory exists
        self.output_dir = os.path.join(self.work_dir, "output")
        os.makedirs(self.output_dir, exist_ok=True)

    def config_path(self):
        return os.path.join(self.work_dir, "scylla.yaml")

    def write_config(self):
        cb = ConfigBuilder(
            self.work_dir, self.seeds, self.native_transport_port, self.native_shard_aware_transport_port
        )
        config_str = cb.build()
        with open(self.config_path(), "w") as f:
            f.write(config_str)

    def start(self):
        print(f"starting node {self.name} with ip {self.listen_address} and api port {self.api_port}")
        self.write_config()

        cmd = [scylla_dir, "--options-file", self.config_path(), "--listen-address", self.listen_address, "--api-address", "0.0.0.0", "--api-port", str(self.api_port)]
        cmd.extend(self.extra_scylla_command_line_args)

        log_file = os.path.join(self.output_dir, f"{self.name}.log")
        log = open(log_file, "w")

        print(f"starting node {self.name} with command: {' '.join(cmd)}, logging to {log_file}")
        self.process = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT
        )
        if self.process.poll() is not None:
            raise RuntimeError(f"failed to start node {self.name}, process exited with code {self.process.returncode}")

        print(f"started node {self.name} ({self.listen_address}:{self.api_port}) pid={self.process.pid}")

    def stop(self):
        if self.process and self.process.poll() is None:
            print(f"stopping node {self.name}")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print(f"node {self.name} did not exit gracefully, killing")
                self.process.kill()


# helpers for configuration file parsing -------------------------------------------------

def load_nodes_config(path):
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("nodes", [])


def shutdown(nodes):
    for n in nodes:
        n.stop()


# main entry point ------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Start one or more local Scylla nodes for testing."
    )
    parser.add_argument(
        "--nodes-config",
        default="nodes.yaml",
        help="YAML file describing the nodes to launch",
    )
    args = parser.parse_args()

    if not os.path.exists(args.nodes_config):
        print(f"nodes config {args.nodes_config} not found, creating default with single node")
        default = {
            "nodes": [
                {
                    "name": "node1",
                    "ip": "127.0.0.1",
                    "api_port": 10000,
                    "work_dir": "./workdir",
                }
            ]
        }
        with open(args.nodes_config, "w") as f:
            yaml.dump(default, f)

    raw_nodes = load_nodes_config(args.nodes_config)

    # build the seeds string from the list of IPs so that each node
    # sees all other nodes when it starts.
    seeds_list = []

    nodes = []


    atexit.register(shutdown, nodes)

    def sigterm_handler(signum, frame):
        shutdown(nodes)
        sys.exit(0)

    signal.signal(signal.SIGINT, sigterm_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)

    for n in raw_nodes:
        name = n.get("name", f"node{len(nodes)+1}")
        ip = n.get("ip")
        api_port = n.get("api_port")
        native_transport_port = n.get("native_transport_port")
        native_shard_aware_transport_port = n.get("native_shard_aware_transport_port")

        print(f"configuring node {name} with ip {ip} and api port {api_port}")

        # Subdir for all nodes
        wdir = os.path.join(output_parent_dir, name)
        if not os.path.isabs(wdir):
            wdir = os.path.abspath(wdir)


        os.makedirs(wdir, exist_ok=True)
        print(f"node {name} work dir: {wdir}")

        seeds_list.append(ip)

        node = ScyllaNode(
            name=name,
            listen_address=ip,
            api_port=api_port,
            native_transport_port=native_transport_port,
            native_shard_aware_transport_port=native_shard_aware_transport_port,
            work_dir=wdir,
            seeds=",".join(seeds_list),
            extra_scylla_command_line_args=n.get("extra_scylla_command_line_args", []),
        )
        nodes.append(node)

        node.start()


# wait for all nodes to be up by polling nodetool status, with a timeout of 30 seconds per node

    for n in nodes:
        start_time = time.time()
        while True:
            try:
                result = subprocess.run(
                    [scylla_dir, "nodetool", "status", "--host", n.listen_address, "--port", str(n.api_port)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )
                print(f"nodetool status for node {n.name} returned with code {result.returncode}")
                print(f"nodetool status output for node {n.name}:\n{result.stdout}")
                print(f"nodetool status error output for node {n.name}:\n{result.stderr}")
                if result.returncode == 0:
                    print(f"node {n.name} is up")
                    break
                else:
                    # non-zero return indicates either not yet up or wrong
                    # port; sleep and retry.
                    # (stderr is captured if needed for debugging.)
                    pass
            except FileNotFoundError:
                # nodetool isn't installed/accessible; skip the check.
                print("nodetool not found, skipping status check")
                break
            except subprocess.TimeoutExpired:
                # nodetool invocation took too long, try again until timeout
                pass
            except Exception as e:
                print(f"error checking status of node {n.name}: {e}")
            if time.time() - start_time > 30:
                print(f"node {n.name} did not start within 30 seconds, exiting")
                shutdown(nodes)
                sys.exit(1)
            time.sleep(1)

    # configure prometheus targets file with the list of nodes and their api ports
    targets_data = {
        "targets": [f"{n.listen_address}:9180" for n in nodes],
        "labels": {
            "cluster": "cluster1",
            "dc": "datacenter1"
        }
    }
    targets_template = yaml.dump([targets_data], default_flow_style=False)

    with open(scylla_servers_file, "w") as f:
        f.write(targets_template)

    print(f"all nodes are up, targets file written to {scylla_servers_file}")
    print("press Ctrl+C to stop the nodes and exit")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
