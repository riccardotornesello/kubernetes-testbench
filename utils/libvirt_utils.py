"""libvirt helpers: the networks of the VM nodes (minikube kvm2)."""

import hashlib
import ipaddress
import tempfile
import xml.etree.ElementTree as ET

from core.log import get_logger
from utils.commands import CommandError, run_command

log = get_logger(__name__)

# Connection used by the minikube kvm2 driver
LIBVIRT_URI = "qemu:///system"

_DNSMASQ_NS = "http://libvirt.org/schemas/network/dnsmasq/1.0"


def bridge_name(network_name: str) -> str:
    """Bridge of a network: stable, unique and within the 15 chars limit."""
    return "tb-" + hashlib.sha256(network_name.encode()).hexdigest()[:10]


def _virsh(*args: str, capture: bool = True):
    return run_command(["virsh", "-c", LIBVIRT_URI, *args], capture=capture)


def network_xml(name: str, cidr: str, gateway: str, pool: str) -> str:
    """
    Definition of a network routed by the host (`open` forward mode: libvirt
    adds no NAT and no firewall rule, the testbench manages them). Its DHCP
    server gives the VMs no default route (the minikube VMs reach the
    Internet through their other interface, on the libvirt `default`
    network) and a route to the testbench pool through the host.
    """
    subnet = ipaddress.IPv4Network(cidr)
    hosts = list(subnet.hosts())
    ET.register_namespace("dnsmasq", _DNSMASQ_NS)
    root = ET.Element("network")
    ET.SubElement(root, "name").text = name
    ET.SubElement(root, "forward", mode="open")
    ET.SubElement(root, "bridge", name=bridge_name(name), stp="on", delay="0")
    ip = ET.SubElement(root, "ip", address=gateway, netmask=str(subnet.netmask))
    dhcp = ET.SubElement(ip, "dhcp")
    ET.SubElement(dhcp, "range", start=str(hosts[1]), end=str(hosts[-1]))
    options = ET.SubElement(root, f"{{{_DNSMASQ_NS}}}options")
    for value in (
        # No router: the default route stays on the `default` network
        "dhcp-option=option:router",
        # Classless static route (RFC 3442) to the testbench pool
        f"dhcp-option=option:classless-static-route,{pool},{gateway}",
    ):
        ET.SubElement(options, f"{{{_DNSMASQ_NS}}}option", value=value)
    return ET.tostring(root, encoding="unicode")


def _summary(xml: str) -> tuple:
    """Fields of a network definition compared to detect a change."""
    root = ET.fromstring(xml)
    forward = root.find("forward")
    bridge = root.find("bridge")
    ip = root.find("ip")
    dhcp_range = root.find("ip/dhcp/range")
    options = root.findall(f"{{{_DNSMASQ_NS}}}options/{{{_DNSMASQ_NS}}}option")
    return (
        forward.get("mode") if forward is not None else None,
        bridge.get("name") if bridge is not None else None,
        ip.get("address") if ip is not None else None,
        ip.get("netmask") if ip is not None else None,
        dhcp_range.get("start") if dhcp_range is not None else None,
        dhcp_range.get("end") if dhcp_range is not None else None,
        tuple(option.get("value") for option in options),
    )


def _current_xml(name: str) -> str | None:
    """Definition of the network `name`, or None if it does not exist."""
    try:
        return _virsh("net-dumpxml", name).stdout
    except CommandError:
        return None


def _is_active(name: str) -> bool:
    result = _virsh("net-info", name).stdout
    return any(
        line.split(":", 1)[1].strip() == "yes"
        for line in result.splitlines()
        if line.startswith("Active:")
    )


def ensure_libvirt_network(name: str, cidr: str, gateway: str, pool: str) -> None:
    """
    Network `name` (see network_xml), defined and started if missing. An
    existing network with another configuration is redefined.
    """
    xml = network_xml(name, cidr, gateway, pool)
    current = _current_xml(name)
    if current is not None and _summary(current) != _summary(xml):
        log.info("Network configuration changed, recreating it", network=name)
        if _is_active(name):
            _virsh("net-destroy", name)
        _virsh("net-undefine", name)
        current = None

    if current is None:
        with tempfile.NamedTemporaryFile("w", suffix=".xml") as f:
            f.write(xml)
            f.flush()
            _virsh("net-define", f.name)

    if not _is_active(name):
        _virsh("net-start", name)
