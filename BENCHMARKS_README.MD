# How to run benchmarks with Scylla Monitoring Stack

## Prepare the environment

1. Prepare your `./docker-compose.yaml`:

    - Observability stack:

        ```yaml
        services:
        grafana:
            container_name: agraf
            environment:
            - GF_PANELS_DISABLE_SANITIZE_HTML=true
            - GF_PATHS_PROVISIONING=/var/lib/grafana/provisioning
            - GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=scylladb-scylla-datasource
            # This is where you set Grafana security
            - GF_AUTH_BASIC_ENABLED=false
            - GF_AUTH_ANONYMOUS_ENABLED=true
            - GF_AUTH_ANONYMOUS_ORG_ROLE=Admin
            - GF_SECURITY_ADMIN_PASSWORD=admin
            # To set your home dashboard uncomment the following line, set VERSION to be your current version
            #- GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/ver_VERSION/scylla-overview.VERSION.json
            image: grafana/grafana:12.3.1
            ports:
            - 3000:3000
            user: 1000:1000
            volumes:
            - ./grafana/build:/var/lib/grafana/dashboards
            - ./grafana/plugins:/var/lib/grafana/plugins
            - ./grafana/provisioning:/var/lib/grafana/provisioning
            # Uncomment the following line for grafana persistency
            # - path/to/grafana/dir:/var/lib/grafana
        loki:
            command:
            - --config.file=/mnt/config/loki-config.yaml
            container_name: loki
            image: grafana/loki:3.6.3
            ports:
            - 3100:3100
            volumes:
            - ./loki/rules:/etc/loki/rules
            - ./loki/conf:/mnt/config
        promotheus:
            command:
            - --config.file=/etc/prometheus/prometheus.yml
            container_name: aprom
            image: prom/prometheus:v3.8.1
            ports:
            - 9090:9090
            volumes:
            - ./prometheus/build/prometheus.yml:/etc/prometheus/prometheus.yml
            - ./prometheus/prom_rules/:/etc/prometheus/prom_rules/
            # instead of the following three targets, you can place three files under one directory and mount that directory
            # If you do, uncomment the following line and delete the three lines afterwards
            #- /path/to/targets:/etc/scylla.d/prometheus/targets/
            - ./prometheus/scylla_servers.yml:/etc/scylla.d/prometheus/targets/scylla_servers.yml:z
            - ./prometheus/scylla_manager_servers.yml:/etc/scylla.d/prometheus/targets/scylla_manager_servers.yml:z
            - ./prometheus/scylla_servers.yml:/etc/scylla.d/prometheus/targets/node_exporter_servers.yml:z
        ```

    - if you want to preserve metrics, look at grafana's volumes section:

        ```yaml
        # Uncomment the following line for grafana persistency
        # - path/to/grafana/dir:/var/lib/grafana
        ```

    - Examplary scylla config:

        ```yaml:
        scylla2:
            image: scylladb/scylla:2026.1.0-rc1
            ports:
            - "49181:9180"
            - "12346:10000"
            command: --smp 4 --memory 1G --api-address 0.0.0.0 --seeds scylla1 --reactor-backend=epoll
            restart: on-failure:2
            healthcheck:
            test: ["CMD-SHELL", "[ $$(nodetool statusgossip) = running ]"]
            interval: 10s
            timeout: 5s
            retries: 10
            start_period: 20s
        ```

2. Create `./prometheus/scylla_servers.yml`:

    - you can just copy the content of `./prometheus/scylla_servers.yml.example` and adjust it to your needs
    - make sure that labels are correct, you can check the proper values by runnign `scylla nodetool status` against each node

        ```yaml
        - targets:
            - scylla1:9180
        labels:
            cluster: cluster1
            dc: datacenter1
        - targets:
            - scylla2:9180
        labels:
            cluster: cluster1
            dc: datacenter1
        ```

3. Run:

    > ./start-all.sh --no-alertmanager -v 2025.4 -compose

    This will generate necessary configuration files and start the stack. You can just kill it after it starts:

    > ./kill-all.sh

4. Run `docker compose up` and wait until all services are healthy. You can check the status of each service by running `docker ps` and looking at the "STATUS" column. For scylla nodes, you should see "healthy" in the status.

5. Once all services are healthy, you can access Grafana at `http://localhost:3000` (or the port you specified in the `docker-compose.yaml`) and start creating dashboards to visualize your ScyllaDB metrics.

6. If everything is up smoothly, you can run your benchmarks against the scylla nodes and see the metrics in Grafana.

    ```yaml
    cassandra:
        image: scylladb/cassandra-stress:latest
        command: write -node scylla1
        depends_on:
        scylla1:
            condition: service_healthy
        scylla2:
            condition: service_healthy
    ```

## Troubleshooting

1. Check for ports conflict.

2. See if all scylla targets for prometheus are up and running by accessing `http://localhost:9090/targets` (or the port you specified for prometheus) and looking for the "scylla_servers" target. If the target is down, check the logs of your scylla nodes to see if there are any issues with the health checks or if the nodes are not starting properly.

3. See logs for scylla and if they started correctly

4. Do not specify `--listen-address 0.0.0.0` for scylla