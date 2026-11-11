"""Summary of the created clusters, shown at the end of the run."""

import sys
from typing import Any, TextIO

from core.log import get_logger
from runtimes.base import NODE_TIER_LABEL
from utils.kubeconfig import get_kubeconfig_location
from utils.kubernetes_utils import list_node_addresses

log = get_logger("testbench")


def collect_nodes(runtimes: dict[str, Any]) -> list[dict[str, str]]:
    """One row per node of every cluster, in configuration order."""
    rows = []
    for name, runtime in runtimes.items():
        network = runtime.settings.network
        for node in list_node_addresses(get_kubeconfig_location(name)):
            rows.append(
                {
                    "cluster": name,
                    "runtime": runtime.settings.runtime,
                    "network": network.name,
                    "cidr": network.cidr,
                    "node": node["name"],
                    "roles": node["roles"],
                    "tier": node["labels"].get(NODE_TIER_LABEL, ""),
                    "ip": node["ip"],
                }
            )
    return rows


def format_table(rows: list[dict[str, str]]) -> str:
    """Rows as a plain text table, one block per cluster."""
    columns = [("NODE", "node"), ("ROLES", "roles"), ("TIER", "tier"), ("IP", "ip")]
    widths = {
        key: max([len(title)] + [len(row[key]) for row in rows])
        for title, key in columns
    }

    def line(values: dict[str, str]) -> str:
        return (
            "  "
            + "  ".join(values[key].ljust(widths[key]) for _, key in columns).rstrip()
        )

    lines = []
    clusters = list(dict.fromkeys(row["cluster"] for row in rows))
    for cluster in clusters:
        cluster_rows = [row for row in rows if row["cluster"] == cluster]
        first = cluster_rows[0]
        if lines:
            lines.append("")
        lines.append(
            f"{cluster} ({first['runtime']}, network {first['network']} "
            f"{first['cidr']})"
        )
        lines.append(line({key: title for title, key in columns}))
        lines.extend(line(row) for row in cluster_rows)
    return "\n".join(lines)


def report_nodes(
    runtimes: dict[str, Any], log_format: str = "console", out: TextIO | None = None
) -> None:
    """
    Shows the address of every node: a table on stdout with the console log
    format, one log record per node with the json one.
    """
    rows = collect_nodes(runtimes)
    if log_format == "json":
        for row in rows:
            log.info("Node", **row)
        return
    out = out or sys.stdout
    out.write("\n" + format_table(rows) + "\n")
    out.flush()
